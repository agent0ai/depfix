from depfix import import_module

requests = import_module("requests>=2.31,<3")
print(requests.__depfix_version__)
