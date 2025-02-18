#!/usr/bin/env python3.6


from configVar import config_vars
from .instlClient import InstlClient
from pybatch import Stage


class InstlClientSync(InstlClient):
    def __init__(self, initial_vars) -> None:
        super().__init__(initial_vars)
        self.read_defaults_file(super().__thisclass__.__name__)

    def do_sync(self):
        repo_type = config_vars["REPO_TYPE"].str()
        if repo_type == "URL":
            from .instlInstanceSync_url import InstlInstanceSync_url

            syncer = InstlInstanceSync_url(self)
        elif repo_type == "BOTO":
            from .instlInstanceSync_boto import InstlInstanceSync_boto

            syncer = InstlInstanceSync_boto(self)
        elif repo_type == "SVN":
            from .instlInstanceSync_svn import InstlInstanceSync_svn

            syncer = InstlInstanceSync_svn(self)
        elif repo_type == "P4":
            from .instlInstanceSync_p4 import InstlInstanceSync_p4

            syncer = InstlInstanceSync_p4(self)
        else:
            raise ValueError('REPO_TYPE is not defined in input file')

        self.read_defaults_file(type(syncer).__name__)
        syncer.init_sync_vars()
        self.batch_accum.set_current_section('sync')

        syncer.create_sync_instructions()
