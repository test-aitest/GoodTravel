import os
import sys

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSButton,
    NSMakeRect,
    NSTextField,
    NSTimer,
    NSWindow,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject

TITLE = "ClientOps test window"
LIFETIME_SECONDS = 60.0


class Counter(NSObject):
    def initWithField_(self, field):
        self = objc.super(Counter, self).init()
        if self is None:
            return None
        self.field = field
        return self

    def addOne_(self, sender):
        text = str(self.field.stringValue()).strip() or "0"
        self.field.setStringValue_(str(int(text) + 1))


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(40, 40, 300, 110), NSWindowStyleMaskTitled, NSBackingStoreBuffered, False
    )
    window.setTitle_(TITLE)
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 40, 120, 24))
    field.setStringValue_("1")
    field.setAccessibilityLabel_("Quantity")
    counter = Counter.alloc().initWithField_(field)
    button = NSButton.buttonWithTitle_target_action_("Add one", counter, "addOne:")
    button.setFrame_(NSMakeRect(160, 38, 110, 28))
    window.contentView().addSubview_(field)
    window.contentView().addSubview_(button)
    window.orderFrontRegardless()
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(LIFETIME_SECONDS, False, lambda timer: app.terminate_(None))
    print(os.getpid(), flush=True)
    sys.stdout.close()
    app.run()


if __name__ == "__main__":
    main()
