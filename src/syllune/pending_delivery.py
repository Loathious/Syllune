# One deliberate click delivers one durable pending dictation, oldest first.
# Persist a claim before touching another app so a crash cannot silently paste
# the same item again on restart. Recognition and queue delivery never overlap.
import threading


class PendingDelivery:
    def __init__(self, entries, update, deliver, allowed, current_target, on_change=lambda: None):
        self.entries, self.update, self.deliver = entries, update, deliver
        self.allowed, self.current_target, self.on_change = allowed, current_target, on_change
        self._lock = threading.Lock()

    # A stale target or a concurrent click leaves the journal untouched.
    def insert(self, target):
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if not target or not self.allowed(target):
                return False
            current = self.current_target()
            if not current or current.get('target_token') != target.get('target_token'):
                return False
            entries = self.entries()
            if not entries:
                return False
            item = entries[0]
            if not self.update(item['id'], 'delivering'):
                return False
            try:
                success = bool(self.deliver(item['text']))
            except Exception:
                self.update(item['id'], 'pending')
                self.on_change()
                return False
            self.update(item['id'], 'inserted' if success else 'pending')
            self.on_change()
            return success
        finally:
            self._lock.release()
