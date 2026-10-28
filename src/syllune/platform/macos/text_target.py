# Pending text stays queued on macOS until a verified accessibility-based
# editable-click implementation is available. Unknown targets fail closed.
def current_text_target():
    return None


class TextTargetWatcher:
    def __init__(self, on_target):
        self.on_target = on_target

    def start(self):
        pass

    def stop(self):
        pass
