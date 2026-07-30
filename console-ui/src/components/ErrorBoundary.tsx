/** Error boundary around page content: a render crash must not unmount the
 * whole SPA into a dead white screen (2026-07-29: a hook placed after the
 * loading early-return fired only on the loading→ready transition and took
 * the entire app down). The nav chrome survives, the message is readable,
 * and Shell keys this boundary by pathname so route changes reset it. */
import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  title: string;
  hint: string;
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('portal page crash:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card warn" style={{ marginTop: '1rem' }}>
          <p className="sm">
            <strong>{this.props.title}</strong>
          </p>
          <p className="tiny muted mono">
            {String(this.state.error.message ?? this.state.error)}
          </p>
          <p className="tiny muted" style={{ marginBottom: 0 }}>
            {this.props.hint}
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
