import depfix

with depfix.using("idna==3.6"):
    import idna as idna_36

with depfix.using("idna==3.7"):
    import idna as idna_37

assert idna_36 is not idna_37
assert idna_36.__version__ == "3.6"
assert idna_37.__version__ == "3.7"
assert idna_36.__name__ != idna_37.__name__

print("old:", idna_36.__version__, idna_36.__name__)
print("new:", idna_37.__version__, idna_37.__name__)
