import { Component, type ReactNode } from "react";

/** A crash in one area (for example a browser without WebGL for the 3D view) must not blank the other pages. */
export default class ErrorBoundary extends Component<{ what: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="boundary" role="alert">
        <h2>{this.props.what} could not start</h2>
        <p className="muted">{/webgl/i.test(this.state.error.message) ? "This browser cannot show 3D graphics (WebGL is off or unavailable). Try another browser or turn on hardware acceleration." : this.state.error.message}</p>
        <p className="muted">The other pages still work.</p>
      </div>
    );
  }
}
