#!/usr/bin/env python2.7

from __future__ import print_function
import datetime

from pyinstl.log_utils import func_log_wrapper
from pyinstl.utils import *

from instlInstanceBase import InstlInstanceBase
from pyinstl import svnTree

map_info_extension_to_format = {"txt" : "text", "text" : "text",
                "inf" : "info", "info" : "info",
                "yml" : "yaml", "yaml" : "yaml",
                "pick" : "pickle", "pickl" : "pickle", "pickle" : "pickle",
                "props" : "props", "prop" : "props"
                }

class InstlAdmin(InstlInstanceBase):

    def __init__(self, initial_vars):
        super(InstlAdmin, self).__init__(initial_vars)
        self.cvl.set_variable("__ALLOWED_COMMANDS__").extend( ('trans', 'createlinks') )
        self.svnTree = svnTree.SVNTree()

    @func_log_wrapper
    def do_command(self):
        the_command = self.cvl.get_str("__MAIN_COMMAND__")
        if the_command in self.cvl.get_list("__ALLOWED_COMMANDS__"):
            #print("server_commands", the_command)
            if the_command == "trans":
                self.read_info_map_file(self.cvl.get_str("__MAIN_INPUT_FILE__"))
                if "__PROPS_FILE__" in self.cvl:
                    self.read_info_map_file(self.cvl.get_str("__PROPS_FILE__"))
                self.filter_out_info_map(self.cvl.get_list("__FILTER_OUT_PATHS__"))
                self.write_info_map_file()
            elif the_command == "createlinks":
                self.create_links()


    def read_info_map_file(self, in_file_path):
        _, extension = os.path.splitext(in_file_path)
        input_format = map_info_extension_to_format[extension[1:]]
        self.svnTree.comments.append("Original file "+in_file_path)
        self.svnTree.comments.append("      read on "+datetime.datetime.today().isoformat())
        self.svnTree.read_info_map_from_file(in_file_path, format=input_format)

    def write_info_map_file(self):
        _, extension = os.path.splitext(self.cvl.get_str("__MAIN_OUT_FILE__"))
        output_format = map_info_extension_to_format[extension[1:]]
        self.svnTree.write_to_file(self.cvl.get_str("__MAIN_OUT_FILE__"), in_format=output_format)

    def filter_out_info_map(self, paths_to_filter_out):
        for path in paths_to_filter_out:
            self.svnTree.remove_item_at_path(path)

    def create_links(self):
        if "COPY_TOOL" not in self.cvl:
            from platformSpecificHelper_Base import DefaultCopyToolName
            self.cvl.set_variable("COPY_TOOL").append(DefaultCopyToolName(self.cvl.get_str("TARGET_OS")))
        if "SVN_CLIENT_PATH" not in self.cvl:
            self.cvl.set_variable("SVN_CLIENT_PATH").append("svn")

        self.platform_helper.use_copy_tool(self.cvl.get_str("COPY_TOOL"))

        checkout_folder_path = "$(__ROOT_LINKS_FOLDER__)/Base"
        revision_folder_path = "$(__ROOT_LINKS_FOLDER__)/$(__REPO_REV__)"
        revision_instl_folder_path = revision_folder_path+"/instl"

        self.batch_accum.set_current_section('admin')
        self.batch_accum += self.platform_helper.mkdir(checkout_folder_path)
        self.batch_accum += self.platform_helper.echo("Getting version $(__REPO_REV__) from $(__SVN_URL__)")
        checkout_command_parts = ['"$(SVN_CLIENT_PATH)"', "co", '"'+"$(__SVN_URL__)@$(__REPO_REV__)"+'"', '"'+checkout_folder_path+'"', "--depth", "infinity"]
        self.batch_accum += " ".join(checkout_command_parts)

        self.batch_accum += self.platform_helper.mkdir("$(__ROOT_LINKS_FOLDER__)/$(__REPO_REV__)")
        self.batch_accum += self.platform_helper.echo("Copying version $(__REPO_REV__) to $(__ROOT_LINKS_FOLDER__)/$(__REPO_REV__)")
        self.batch_accum += self.platform_helper.copy_tool.copy_dir_contents_to_dir(checkout_folder_path, revision_folder_path)

        self.batch_accum += self.platform_helper.mkdir(revision_instl_folder_path)
        self.batch_accum += self.platform_helper.cd(checkout_folder_path)
        self.batch_accum += self.platform_helper.echo("Getting info from svn to ../$(__REPO_REV__)/instl/info_map.info")
        info_command_parts = ['"$(SVN_CLIENT_PATH)"', "info", "--depth infinity", ">", "../$(__REPO_REV__)/instl/info_map.info"]
        self.batch_accum += " ".join(info_command_parts)

        self.batch_accum += self.platform_helper.echo("Getting props from svn to ../$(__REPO_REV__)/instl/info_map.props")
        props_command_parts = ['"$(SVN_CLIENT_PATH)"', "proplist", "--depth infinity", ">", "../$(__REPO_REV__)/instl/info_map.props"]
        self.batch_accum += " ".join(props_command_parts)

        self.batch_accum += self.platform_helper.echo("Creating info_map.txt to ../$(__REPO_REV__)/instl/info_map.txt")
        trans_command_parts = ['"$(__INSTL_EXE_PATH__)"', "trans", "--in", "../$(__REPO_REV__)/instl/info_map.info", "--props ", "../$(__REPO_REV__)/instl/info_map.props", "--out ", "../$(__REPO_REV__)/instl/info_map.txt"]
        self.batch_accum += " ".join(trans_command_parts)

        self.batch_accum += self.platform_helper.echo("Creating info_map_Mac.txt to ../$(__REPO_REV__)/instl/info_map_Mac.txt")
        trans_command_parts = ['"$(__INSTL_EXE_PATH__)"', "trans", "--in", "../$(__REPO_REV__)/instl/info_map.txt", "--out ", "../$(__REPO_REV__)/instl/info_map_Mac.txt",  "--filter-out", "Win"]
        self.batch_accum += " ".join(trans_command_parts)

        trans_command_parts = ['"$(__INSTL_EXE_PATH__)"', "trans", "--in", "../$(__REPO_REV__)/instl/info_map.txt", "--out ", "../$(__REPO_REV__)/instl/info_map_Win.txt",  "--filter-out", "Mac"]
        self.batch_accum += " ".join(trans_command_parts)

        self.batch_accum += self.platform_helper.echo("done $(__REPO_REV__)")
        self.create_variables_assignment()
        self.write_batch_file()
