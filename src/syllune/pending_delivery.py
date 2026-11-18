# A dictation that found no text field waits here, already on the clipboard.
# Nothing is typed by itself: the pill asks first, and only the newest waiting
# dictation is ever pasted. A claim is persisted before another app is touched,
# so a crash cannot paste the same item again on restart.
import threading


class PendingDelivery:
    # `entries()` lists the waiting dictations oldest first, `update(id, status)`
    # persists a delivery state, `deliver(text)` pastes, `allowed(target)` says
    # whether pasting into that window is acceptable right now.
    def __init__(self, entries, update, deliver, allowed, on_change=lambda: None):
        self.entries, self.update, self.deliver = entries, update, deliver
        self.allowed, self.on_change = allowed, on_change
        self._lock = threading.Lock()

    # The dictation an offer is about: the newest one still waiting.
    def latest(self):
        entries = self.entries()
        return entries[-1] if entries else None

    # The user said yes: paste the newest waiting dictation into `target`.
    # Older ones were superseded by it and stay in History only.
    def insert(self, target):
        if not self._lock.acquire(blocking=False):
            return False
        try:
            if not target or not self.allowed(target):
                return False
            entries = self.entries()
            if not entries:
                return False
            item = entries[-1]
            if not self.update(item['id'], 'delivering'):
                return False
            for older in entries[:-1]:
                self.update(older['id'], 'failed')
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

    # The user pasted `text` some other way (the paste-last hotkey), so the
    # waiting copy of it must not be offered again.
    def settle(self, text):
        entries = self.entries()
        if not entries or entries[-1]['text'] != text:
            return False
        for older in entries[:-1]:
            self.update(older['id'], 'failed')
        self.update(entries[-1]['id'], 'inserted')
        self.on_change()
        return True

    # Stop offering everything that waits; it all stays in History.
    def retire_all(self):
        entries = self.entries()
        for entry in entries:
            self.update(entry['id'], 'failed')
        if entries:
            self.on_change()
        return len(entries)
