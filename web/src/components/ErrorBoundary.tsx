// One broken window must never take the rest of the editor with it.
//
// Every child of the overlay and every surface root is wrapped in a Boundary:
// a React error boundary that catches a throw from its subtree, logs the error
// and the component stack, and shows a small card naming the window with
// "Copy error", "Try again" and "Dismiss". Everything outside the card keeps
// working (the ⋯ menu above all: an unmounted overlay root is a dead editor
// until the page is reloaded).

import { Component, type ErrorInfo, type ReactNode } from "react";
import { copyText } from "../actions";

interface Props {
  /** what broke, in the card's words: "Inspector", "h3 Shots", … */
  name: string;
  children?: ReactNode;
}

interface State {
  error: Error | null;
  stack: string;
  dismissed: boolean;
}

export class Boundary extends Component<Props, State> {
  state: State = { error: null, stack: "", dismissed: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    const stack = info.componentStack ?? "";
    this.setState({ stack });
    console.error(`[h3pipe] ${this.props.name} crashed:`, error, stack);
  }

  /** Everything worth pasting into a bug report. */
  report(): string {
    const e = this.state.error;
    return [
      `h3pipe editor: ${this.props.name} crashed`,
      String(e?.stack || e),
      this.state.stack && `Component stack:${this.state.stack}`,
    ].filter(Boolean).join("\n");
  }

  render() {
    const { error, dismissed } = this.state;
    if (!error) return this.props.children;
    if (dismissed) return null;
    return (
      <div className="h3-crash h3-root">
        <div className="h3-row">
          <i className="pi pi-exclamation-triangle" />
          <b className="h3-grow">{this.props.name} stopped</b>
          <button
            className="h3-btn h3-icon"
            title="Hide this (the rest of the editor keeps working)"
            onClick={() => this.setState({ dismissed: true })}
          >
            <i className="pi pi-times" />
          </button>
        </div>
        <div className="h3-small h3-ell" title={String(error?.message || error)}>{String(error?.message || error)}</div>
        <div className="h3-row">
          <button className="h3-btn" title="Copy the error and the component stack" onClick={() => void copyText(this.report(), "Error")}>
            <i className="pi pi-copy" /> Copy error
          </button>
          <button
            className="h3-btn"
            title="Render it again (it breaks again if nothing has changed)"
            onClick={() => this.setState({ error: null, stack: "", dismissed: false })}
          >
            <i className="pi pi-refresh" /> Try again
          </button>
        </div>
        <div className="h3-small h3-muted">The console has the full stack.</div>
      </div>
    );
  }
}
