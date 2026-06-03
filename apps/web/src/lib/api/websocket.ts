type MessageHandler = (data: unknown) => void;

export class MemoryBridgeSocket {
  private ws: WebSocket | null = null;
  private handlers = new Map<string, Set<MessageHandler>>();
  private reconnectAttempts = 0;
  private maxRetries = 5;

  connect(token: string) {
    const url = process.env.NEXT_PUBLIC_WS_URL || "wss://memory-bridge-app-production.up.railway.app/ws";
    this.ws = new WebSocket(`${url}?token=${token}`);

    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const type = msg.type || "message";
        const handlers = this.handlers.get(type);
        if (handlers) {
          handlers.forEach((h) => h(msg.payload));
        }
      } catch {
        // ignore malformed messages
      }
    };

    this.ws.onclose = () => {
      if (this.reconnectAttempts < this.maxRetries) {
        this.reconnectAttempts++;
        setTimeout(() => this.connect(token), 1000 * Math.pow(2, this.reconnectAttempts));
      }
    };
  }

  on(event: string, handler: MessageHandler) {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(handler);
  }

  off(event: string, handler: MessageHandler) {
    this.handlers.get(event)?.delete(handler);
  }

  send(type: string, payload: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, payload }));
    }
  }

  disconnect() {
    this.ws?.close();
    this.ws = null;
    this.handlers.clear();
  }
}

export const socket = new MemoryBridgeSocket();
