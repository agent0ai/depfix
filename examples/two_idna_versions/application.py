from depfix import import_module

idna_36 = import_module("idna==3.6")
idna_37 = import_module("idna==3.7")

assert idna_36 is not idna_37
assert idna_36.__version__ == "3.6"
assert idna_37.__version__ == "3.7"
assert idna_36.__name__ != idna_37.__name__

print("old:", idna_36.__version__, idna_36.__name__)
print("new:", idna_37.__version__, idna_37.__name__)
