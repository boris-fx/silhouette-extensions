import fx

if fx.gui:
    from .colors import PREF, DEFAULTS, encode, object_created, UpdateNodeColors
    import hook

    fx.prefs.add(PREF, encode(DEFAULTS), option=True, label="Intent Group Colors")
    fx.addAction(UpdateNodeColors())
    hook.add("object_created", object_created)

    def make_options():
        from .options import IntentGroupColorsOptions
        return IntentGroupColorsOptions()

    fx.registerOptionsPanel("com.borisfx.nodecolors", make_options, buttons="close")
