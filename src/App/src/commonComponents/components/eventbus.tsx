// `never[]` rather than `unknown[]`: parameters are checked contravariantly under
// `strict`, so a handler such as `(panel: "first" | null) => void` is assignable to
// `(...args: never[]) => void` but not to `(...args: unknown[]) => void`. This keeps
// the bus accepting arbitrary handlers without reaching for `any`.
type EventCallback = (...args: never[]) => void;

class EventBus {
  private events: { [key: string]: EventCallback[] } = {};
  private panelWidth = 400;
  private activePanel: "first" | "second" | "third" | "fourth" | null = null;

  on(event: string, callback: EventCallback) {
    if (!this.events[event]) {
      this.events[event] = [];
    }
    this.events[event].push(callback);
  }

  off(event: string, callback: EventCallback) {
    if (!this.events[event]) return;
    this.events[event] = this.events[event].filter(cb => cb !== callback);
  }

  emit(event: string, ...args: unknown[]) {
    if (!this.events[event]) return;
    // The bus is deliberately untyped across event names, so the payload cannot be
    // reconciled with each handler's declared parameters here. The cast is erased at
    // compile time and the call is unchanged at runtime.
    this.events[event].forEach(callback =>
      (callback as (...a: unknown[]) => void)(...args)
    );
  }

  // Panel control
  setActivePanel(panel: "first" | "second" | "third" | "fourth" | null) {
    this.activePanel = panel;
    this.emit("setActivePanel", panel);
  }

  getActivePanel(): "first" | "second" | "third" | "fourth" | null {
    return this.activePanel;
  }

  // Shared panel width
  setPanelWidth(width: number) {
    this.panelWidth = width;
    this.emit("panelWidthChanged", width);
  }

  getPanelWidth(): number {
    return this.panelWidth;
  }
}

const eventBus = new EventBus();
export default eventBus;
