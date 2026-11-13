"""The printer interface boundary (A.4). The rest of the system talks to printers only through `PrinterAdapter`.

Today the Bambu LAN connector (`bambu.PrinterLink`) is the only adapter. A printer from another maker, or a gateway process
on another network, implements the same few methods and nothing else changes. Bed state lives next to it in `bedstate.py`.
"""
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class AdapterError(Exception):
    """A problem the user can act on (printer busy, unreachable, refused the command)."""


@runtime_checkable
class PrinterAdapter(Protocol):
    def send_print(self, local_path: Path, remote_name: str, plate: str = ...) -> bool:
        """Transfer a sliced job and start it. True once the printer reports it started. Raises AdapterError."""

    def control(self, action: str) -> bool:
        """Pause, resume or stop the running print ("pause" | "resume" | "stop"). True once the printer reports it. Raises AdapterError."""

    def stop(self) -> None:
        """Close the connection."""


def get(printer_id: str) -> PrinterAdapter | None:
    """The live adapter for a printer, or None for a manual printer (no connection details)."""
    from . import gateways, store
    from .bambu import links
    if (store.printers.get(printer_id) or {}).get("gateway"):
        return gateways.link_for(printer_id)
    return links.get(printer_id)


def status(printer: dict[str, Any]) -> dict[str, Any]:
    """Latest known state, as reported by whichever adapter feeds the printer record."""
    return printer.get("status") or {}
