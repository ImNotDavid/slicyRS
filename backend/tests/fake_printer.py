"""A minimal implicit-FTPS server that behaves like a Bambu printer's file service, for tests."""
import datetime
import socket
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def make_cert(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-printer")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class FakeFTPS:
    def __init__(self, directory: Path, code="12345678", *, drop_control_after_data=False, truncate=False, size_supported=True):
        cert, key = make_cert(directory)
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.code, self.drop, self.truncate, self.size_supported = code, drop_control_after_data, truncate, size_supported
        self.files: dict[str, bytes] = {}
        self.commands: list[str] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._stop = True
        self.sock.close()

    def _accept(self):
        while not self._stop:
            try:
                raw, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(raw,), daemon=True).start()

    def _session(self, raw):
        try:
            conn = self.ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            return
        f = conn.makefile("rw", newline="")
        send = lambda line: (f.write(line + "\r\n"), f.flush())
        data_listener = None
        try:
            send("220 fake bambu ftp")
            authed = False
            while line := f.readline():
                cmd, _, arg = line.strip().partition(" ")
                cmd = cmd.upper()
                self.commands.append(cmd)
                if cmd == "USER":
                    send("331 password please")
                elif cmd == "PASS":
                    authed = arg == self.code
                    send("230 ok" if authed else "530 Login incorrect")
                elif not authed and cmd != "QUIT":
                    send("530 Please login")
                elif cmd in ("PBSZ", "PROT", "TYPE"):
                    send("200 ok")
                elif cmd == "PASV":
                    data_listener = socket.socket()
                    data_listener.bind(("127.0.0.1", 0))
                    data_listener.listen(1)
                    p = data_listener.getsockname()[1]
                    send(f"227 Entering Passive Mode (127,0,0,1,{p >> 8},{p & 255})")
                elif cmd == "STOR":
                    send("150 ok to send")
                    dconn, _ = data_listener.accept()
                    dconn = self.ctx.wrap_socket(dconn, server_side=True)
                    chunks = []
                    while chunk := dconn.recv(65536):
                        chunks.append(chunk)
                    dconn.close()
                    body = b"".join(chunks)
                    self.files[arg] = body[: len(body) // 2] if self.truncate else body
                    if self.drop:
                        return  # Bambu quirk: control connection vanishes after the transfer
                    send("226 Transfer complete")
                elif cmd == "NLST":
                    folder = arg.rstrip("/") + "/"
                    names = "".join(k[len(folder):] + "\r\n" for k in self.files if k.startswith(folder))
                    send("150 listing")
                    dconn, _ = data_listener.accept()
                    dconn = self.ctx.wrap_socket(dconn, server_side=True)
                    dconn.sendall(names.encode())
                    dconn.unwrap()
                    dconn.close()
                    send("226 done")
                elif cmd == "RETR":
                    if arg not in self.files:
                        send("550 no such file")
                        continue
                    send("150 sending")
                    dconn, _ = data_listener.accept()
                    dconn = self.ctx.wrap_socket(dconn, server_side=True)
                    dconn.sendall(self.files[arg])
                    dconn.unwrap()
                    dconn.close()
                    send("226 done")
                elif cmd == "DELE":
                    send("250 deleted" if self.files.pop(arg, None) is not None else "550 no such file")
                elif cmd == "SIZE":
                    if self.size_supported and arg in self.files:
                        send(f"213 {len(self.files[arg])}")
                    else:
                        send("550 no size")
                elif cmd == "QUIT":
                    send("221 bye")
                    return
                else:
                    send("502 not implemented")
        except (ssl.SSLError, OSError, ValueError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
