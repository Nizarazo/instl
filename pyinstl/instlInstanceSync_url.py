#!/usr/bin/env python2.7
from __future__ import print_function

import logging
import hashlib

from pyinstl.log_utils import func_log_wrapper
from pyinstl.utils import *
from pyinstl import svnTree
from instlInstanceSyncBase import InstlInstanceSync

def is_user_data_false_or_dir_empty(svn_item):
    retVal = False
    if svn_item.isFile():
        retVal = svn_item.user_data == False
    elif svn_item.isDir():
        retVal = len(svn_item.subs()) == 0
    return retVal

class InstlInstanceSync_url(InstlInstanceSync):
    """  Class to create sync instruction using static links.
    """
    def __init__(self, instlObj):
        self.instlObj = instlObj      # instance of the instl application
        self.installState = None                # object holding batch instructions
        self.work_info_map = svnTree.SVNTree()  # here most of the work is done: first info map from server is read, later unneeded items
                                                # are filtered out and then items that are already downloaded are filtered out. So finally
                                                # the download instructions are created from the remaining items.
        self.have_map = svnTree.SVNTree()       # info map of what was already downloaded
        self.symlinks = list()

    def init_sync_vars(self):
        """ Prepares variables for sync. Will raise ValueError if a mandatory variable
            is not defined.
        """
        var_description = "from InstlInstanceBase.init_sync_vars"
        if "SYNC_BASE_URL" not in self.instlObj.cvl:
            raise ValueError("'SYNC_BASE_URL' was not defined")
        if "DOWNLOAD_TOOL_PATH" not in self.instlObj.cvl:
            raise ValueError("'DOWNLOAD_TOOL_PATH' was not defined")
        checksum_tool_full_path = self.instlObj.path_searcher.find_file(self.instlObj.cvl.resolve_string("$(CHECKSUM_TOOL_PATH)"), return_original_if_not_found=True)
        self.instlObj.cvl.set_var("CHECKSUM_TOOL_PATH", var_description).append(checksum_tool_full_path)

        self.instlObj.cvl.set_value_if_var_does_not_exist("REPO_REV", "HEAD", description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("SYNC_TRAGET_OS_URL", "$(SYNC_BASE_URL)/$(TARGET_OS)", description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("LOCAL_SYNC_DIR", self.instlObj.get_default_sync_dir(), description=var_description)

        self.instlObj.cvl.set_value_if_var_does_not_exist("BOOKKEEPING_DIR_URL", "$(SYNC_BASE_URL)/instl", description=var_description)
        bookkeeping_relative_path = relative_url(self.instlObj.cvl.get_str("SYNC_BASE_URL"), self.instlObj.cvl.get_str("BOOKKEEPING_DIR_URL"))

        self.instlObj.cvl.set_value_if_var_does_not_exist("INFO_MAP_FILE_URL", "$(SYNC_BASE_URL)/$(REPO_REV)/instl/info_map.txt", description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("LOCAL_BOOKKEEPING_PATH", os.path.join( "$(LOCAL_SYNC_DIR)", "bookkeeping" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("HAVE_INFO_MAP_PATH", os.path.join( "$(LOCAL_BOOKKEEPING_PATH)", "have_info_map.txt" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("NEW_HAVE_INFO_MAP_PATH", os.path.join( "$(LOCAL_BOOKKEEPING_PATH)", "new_have_info_map.txt" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("REQUIRED_INFO_MAP_PATH", os.path.join( "$(LOCAL_BOOKKEEPING_PATH)", "required_info_map.txt" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("TO_SYNC_INFO_MAP_PATH", os.path.join( "$(LOCAL_BOOKKEEPING_PATH)", "to_sync_info_map.txt" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("REPO_REV_LOCAL_BOOKKEEPING_PATH", os.path.join( "$(LOCAL_BOOKKEEPING_PATH)", "$(REPO_REV)" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("LOCAL_COPY_OF_REMOTE_INFO_MAP_PATH", os.path.join( "$(REPO_REV_LOCAL_BOOKKEEPING_PATH)", "remote_info_map.txt" ), description=var_description)
        self.instlObj.cvl.set_value_if_var_does_not_exist("DL_INSTRUCTIONS_TYPE", "one_by_one", description=var_description)

        if "PUBLIC_KEY" not in self.instlObj.cvl:
            if "PUBLIC_KEY_FILE" in self.instlObj.cvl:
                public_key_file = self.instlObj.cvl.get_str("$(PUBLIC_KEY_FILE)")
                public_key_text = open(public_key_file, "rb").read()
                self.instlObj.cvl.set_var("PUBLIC_KEY", "from "+public_key_file).append(public_key_text)

        for identifier in ("SYNC_BASE_URL", "DOWNLOAD_TOOL_PATH", "REPO_REV", "SYNC_TRAGET_OS_URL", "LOCAL_SYNC_DIR", "BOOKKEEPING_DIR_URL",
                           "INFO_MAP_FILE_URL", "LOCAL_BOOKKEEPING_PATH","NEW_HAVE_INFO_MAP_PATH", "REQUIRED_INFO_MAP_PATH",
                            "TO_SYNC_INFO_MAP_PATH", "REPO_REV_LOCAL_BOOKKEEPING_PATH", "LOCAL_COPY_OF_REMOTE_INFO_MAP_PATH",
                            "DL_INSTRUCTIONS_TYPE"):
            #print(identifier, self.instlObj.cvl.get_str(identifier))
            logging.debug("... %s: %s", identifier, self.instlObj.cvl.get_str(identifier))

    def create_sync_instructions(self, installState):
        self.instlObj.batch_accum.set_current_section('sync')
        self.installState = installState
        self.read_remote_info_map()             # reads the full info map from INFO_MAP_FILE_URL and writes it to the sync folder
        self.filter_out_unrequired_items()      # removes items not required to be installed
        self.read_have_info_map()               # reads the info map of items already synced
        self.filter_out_already_synced_items()  # removes items that are already on the user's disk
        ld_instructions_type = self.instlObj.cvl.get_str("DL_INSTRUCTIONS_TYPE") # either config_file or one_by_one
        if ld_instructions_type == "config_file":
            self.create_download_instructions_config_file()
        elif ld_instructions_type == "one_by_one":
            self.create_download_instructions_one_by_one()

    def read_remote_info_map(self):
        """ Reads the info map of the static files available for syncing.
            Writes the map to local sync folder for reference and debugging.
        """
        try:
            safe_makedirs(self.instlObj.cvl.get_str("LOCAL_BOOKKEEPING_PATH"))
            safe_makedirs(self.instlObj.cvl.get_str("REPO_REV_LOCAL_BOOKKEEPING_PATH"))
            need_to_download = True
            if "INFO_MAP_CHECKSUM" in self.instlObj.cvl:
                need_to_download = need_to_download_file(self.instlObj.cvl.get_str("LOCAL_COPY_OF_REMOTE_INFO_MAP_PATH"),
                                                        self.instlObj.cvl.get_str("INFO_MAP_CHECKSUM"))
            if need_to_download:
                download_from_file_or_url(self.instlObj.cvl.get_str("INFO_MAP_FILE_URL"),
                                      self.instlObj.cvl.get_str("LOCAL_COPY_OF_REMOTE_INFO_MAP_PATH"),
                                      public_key=self.instlObj.cvl.get_str("PUBLIC_KEY"),
                                      textual_sig=self.instlObj.cvl.get_str("INFO_MAP_SIG"))
            self.work_info_map.read_info_map_from_file(self.instlObj.cvl.get_str("LOCAL_COPY_OF_REMOTE_INFO_MAP_PATH"), format="text")
        except:
            raise

    def filter_out_unrequired_items(self):
        """ Removes from work_info_map items not required to be installed.
            First all items are marked False.
            Items required by each install source are then marked True.
            Finally items marked False and empty directories are removed.
        """
        self.work_info_map.set_user_data(False, "all")
        for iid  in self.installState.full_install_items:
            installi = self.instlObj.install_definitions_index[iid]
            if installi.source_list():
                for source in installi.source_list():
                    self.mark_required_items_for_source(source)
        self.work_info_map.recursive_remove_depth_first(is_user_data_false_or_dir_empty)
        self.work_info_map.write_to_file(self.instlObj.cvl.get_str("REQUIRED_INFO_MAP_PATH"), in_format="text")

    def read_have_info_map(self):
        """ Reads the map of files previously synced - if there is one.
        """
        if os.path.isfile(self.instlObj.cvl.get_str("HAVE_INFO_MAP_PATH")):
            self.have_map.read_info_map_from_file(self.instlObj.cvl.get_str("HAVE_INFO_MAP_PATH"), format="text")

    def filter_out_already_synced_items(self):
        """ Removes from work_info_map items not required to be synced and updates the in memory have map.
            First all items are marked True.
            Items found in have map are then marked False - provided their have version is equal to tge required version.
            Finally items marked False and empty directories are removed.
            The have map is
        """
        self.work_info_map.set_user_data(True, "all")
        for need_item in self.work_info_map.walk_items(what="file"):
            have_item = self.have_map.get_item_at_path(need_item.full_path_parts())
            if have_item is None:   # not found in have map
                 self.have_map.new_item_at_path(need_item.full_path_parts() , need_item.flags(), need_item.last_rev(), create_folders=True)
            else:                    # found in have map
                if have_item.last_rev() == need_item.last_rev():
                    need_item.user_data = False
                elif have_item.last_rev() < need_item.last_rev():
                    have_item.set_flags(need_item.flags())
                    have_item.set_last_rev(need_item.last_rev())
                elif have_item.last_rev() > need_item.last_rev(): # weird, but need to get the older version
                    have_item.set_flags(need_item.flags())
                    have_item.set_last_rev(need_item.last_rev())
        self.work_info_map.recursive_remove_depth_first(is_user_data_false_or_dir_empty)
        self.work_info_map.write_to_file(self.instlObj.cvl.get_str("TO_SYNC_INFO_MAP_PATH"), in_format="text")
        self.have_map.write_to_file(self.instlObj.cvl.get_str("NEW_HAVE_INFO_MAP_PATH"), in_format="text")

    def mark_required_items_for_source(self, source):
        """ source is a tuple (source_folder, tag), where tag is either !file or !dir """
        target_os_remote_info_map = self.work_info_map.get_item_at_path(self.instlObj.cvl.get_str("TARGET_OS"))
        if target_os_remote_info_map is None:
            raise ValueError(self.instlObj.cvl.get_str("TARGET_OS"), "does not exist in remote map")
        remote_sub_item = target_os_remote_info_map.get_item_at_path(source[0])
        if remote_sub_item is None:
            raise ValueError(source[0], "does not exist in remote map")
        how_to_set = "all"
        if source[1] == '!file':
            if not remote_sub_item.isFile():
                raise  ValueError(source[0], "has type", source[1], "but is not a file")
            how_to_set = "only"
        elif source[1] == '!files':
            if not remote_sub_item.isDir():
                raise ValueError(source[0], "has type", source[1], "but is not a dir")
            how_to_set = "file"
        elif source[1] == '!dir' or source[1] == '!dir_cont': # !dir and !dir_cont are only different when copying
            if not remote_sub_item.isDir():
                raise ValueError(source[0], "has type", source[1], "but is not a dir")
            how_to_set = "all"

        remote_sub_item.set_user_data(True, how_to_set)

    def clear_unrequired_items(self):
        self.work_info_map.recursive_remove_depth_first(is_user_data_false_or_dir_empty)
        # for debugging
        work_info_map_path = self.instlObj.cvl.get_str("REQUIRED_INFO_MAP_PATH")
        self.work_info_map.write_to_file(work_info_map_path, in_format="text")

    def create_download_instructions_one_by_one(self):
        self.instlObj.batch_accum.set_current_section('sync')
        self.instlObj.batch_accum += self.instlObj.platform_helper.progress("from $(SYNC_TRAGET_OS_URL)")
        self.instlObj.batch_accum += self.instlObj.platform_helper.mkdir("$(LOCAL_SYNC_DIR)")
        self.instlObj.batch_accum += self.instlObj.platform_helper.cd("$(LOCAL_SYNC_DIR)")
        self.instlObj.batch_accum.indent_level += 1
        file_list, dir_list = self.work_info_map.sorted_sub_items()
        for need_item in file_list + dir_list:
            self.create_download_instructions_for_item_one_by_one(need_item)
        self.instlObj.batch_accum.indent_level -= 1
        self.instlObj.batch_accum += self.instlObj.platform_helper.new_line()
        self.instlObj.batch_accum += self.instlObj.platform_helper.progress("sync from $(SYNC_TRAGET_OS_URL)")
        self.instlObj.batch_accum += self.instlObj.platform_helper.copy_file_to_file("$(NEW_HAVE_INFO_MAP_PATH)", "$(HAVE_INFO_MAP_PATH)")

    def create_download_instructions_for_item_one_by_one(self, item, path_so_far = list()):
        if item.isSymlink():
            print("Found symlink at", item.full_path())
        elif item.isFile():
            expected_path = os.path.join(*[self.instlObj.cvl.resolve_string("$(LOCAL_SYNC_DIR)")] + path_so_far + [item.name()])
            # check the off chance that the file already exists. This might happen if a previous sync did not finish downloading all it's files
            need_to_download = need_to_download_file(expected_path, item.checksum())
            source_url = '/'.join( ["$(SYNC_BASE_URL)", str(item.last_rev())] + path_so_far + [item.name()] )
            if need_to_download:
                self.instlObj.batch_accum += self.instlObj.platform_helper.dl_tool.download_url_to_file(source_url, item.name())
                self.instlObj.batch_accum += self.instlObj.platform_helper.check_checksum(item.name(), item.checksum())
            if item.name().endswith(".wtar"):
                self.instlObj.batch_accum += self.instlObj.platform_helper.unwtar(item.name())
            self.instlObj.batch_accum += self.instlObj.platform_helper.progress(source_url)
        elif item.isDir():
            path_so_far.append(item.name())
            self.instlObj.batch_accum += self.instlObj.platform_helper.mkdir(item.name())
            self.instlObj.batch_accum += self.instlObj.platform_helper.cd(item.name())
            self.instlObj.batch_accum.indent_level += 1
            file_list, dir_list = item.sorted_sub_items()
            for sub_item in file_list + dir_list:
                self.create_download_instructions_for_item_one_by_one(sub_item, path_so_far)
            self.instlObj.batch_accum.indent_level -= 1
            self.instlObj.batch_accum += self.instlObj.platform_helper.cd("..")
            path_so_far.pop()

    def create_download_instructions_config_file(self):
        self.instlObj.batch_accum.set_current_section('sync')
        num_files = self.work_info_map.num_subs_in_tree(what="file")
        self.instlObj.batch_accum += self.instlObj.platform_helper.progress("from $(SYNC_TRAGET_OS_URL)")
        self.instlObj.batch_accum += self.instlObj.platform_helper.mkdir("$(LOCAL_SYNC_DIR)")
        self.instlObj.batch_accum += self.instlObj.platform_helper.cd("$(LOCAL_SYNC_DIR)")
        self.instlObj.cvl.set_var("__CURL_CONFIG_FILE_NAME__").append("curl_config.txt")
        self.sync_base_url = self.instlObj.cvl.resolve_string("$(SYNC_BASE_URL)")
        self.instlObj.batch_accum.indent_level += 1
        file_list, dir_list = self.work_info_map.sorted_sub_items()
        for need_item in file_list + dir_list:
            self.create_download_instructions_for_item_config_file(need_item)
        curl_config_file_path = self.instlObj.cvl.resolve_string(os.path.join("$(LOCAL_SYNC_DIR)", "$(__CURL_CONFIG_FILE_NAME__)"))
        self.instlObj.platform_helper.dl_tool.create_config_file(curl_config_file_path)
        self.instlObj.batch_accum += self.instlObj.platform_helper.dl_tool.download_from_config_file("$(__CURL_CONFIG_FILE_NAME__)")
        self.instlObj.batch_accum.indent_level -= 1
        self.instlObj.batch_accum += self.instlObj.platform_helper.new_line()
        self.instlObj.batch_accum += self.instlObj.platform_helper.resolve_readlink_files()
        self.instlObj.batch_accum += self.instlObj.platform_helper.new_line()
        self.instlObj.batch_accum += self.instlObj.platform_helper.progress("from $(SYNC_TRAGET_OS_URL)")
        self.instlObj.batch_accum += self.instlObj.platform_helper.copy_file_to_file("$(NEW_HAVE_INFO_MAP_PATH)", "$(HAVE_INFO_MAP_PATH)")


    def create_download_instructions_for_item_config_file(self, item, path_so_far = list()):
        if item.isSymlink():
            print("Found symlink at", item.full_path())
        elif item.isFile():
            expected_path = os.path.join(*[self.instlObj.cvl.resolve_string("$(LOCAL_SYNC_DIR)")] + path_so_far + [item.name()])
            need_to_download = need_to_download_file(expected_path, item.checksum())
            if need_to_download:
                source_url = '/'.join( [ self.sync_base_url, str(item.last_rev())] + path_so_far + [item.name()] )
                self.instlObj.platform_helper.dl_tool.add_download_url( source_url, item.full_path() )
        elif item.isDir():
            path_so_far.append(item.name())
            self.instlObj.batch_accum.indent_level += 1
            file_list, dir_list = item.sorted_sub_items()
            for sub_item in file_list + dir_list:
                self.create_download_instructions_for_item_config_file(sub_item, path_so_far)
            self.instlObj.batch_accum.indent_level -= 1
            path_so_far.pop()
