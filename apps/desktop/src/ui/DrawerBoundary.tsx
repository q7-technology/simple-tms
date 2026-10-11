import { Component, type ErrorInfo, type ReactNode } from "react";
import { Drawer } from "./Drawer";

/** If a drawer throws while drawing, say so plainly over the map rather than
 * leave a blank screen. Keyed by the path, so opening another drawer starts afresh. */
export class DrawerBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("a drawer failed to draw", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Drawer label="Something went wrong" head={<h1>Something went wrong</h1>}>
        <div className="msg r" role="alert">Something went wrong showing this screen. Reload the page to try again.</div>
        <div><button type="button" className="btn primary" onClick={() => window.location.reload()}>Reload</button></div>
      </Drawer>
    );
  }
}
