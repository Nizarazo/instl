import sys
import os
import io
import pathlib
import re
import logging
import time
import datetime

from .baseClasses import PythonBatchCommandBase
from .reportingBatchCommands import Section, PythonBatchRuntime
from configVar import config_vars
import utils

python_batch_log_level = logging.WARNING


first_cap_re = re.compile('(.)([A-Z][a-z]+)')
all_cap_re = re.compile('([a-z0-9])([A-Z])')


def camel_to_snake_case(identifier):
    identifier1 = first_cap_re.sub(r'\1_\2', identifier)
    identifier2 = all_cap_re.sub(r'\1_\2', identifier1).lower()
    return identifier2


def batch_repr(batch_obj):
    assert isinstance(batch_obj, (PythonBatchCommandBase, PythonBatchCommandAccum))
    if sys.platform == "darwin":
        return batch_obj.repr_batch_mac()

    elif sys.platform == "win32":
        return batch_obj.repr_batch_win()


class PythonBatchCommandAccum(PythonBatchCommandBase, essential=True):

    section_order = ("pre", "assign", "begin", "links", "upload", "sync", "post-sync", "copy", "post-copy", "remove", "admin", "end", "post")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_section: str = None
        self.sections = dict()
        #self.context_stack = [list()]
        self.creation_time = time.strftime('%d-%m-%y_%H-%M')

    def clear(self):
        self.sections = dict()
        if self.current_section:
            self.set_current_section(self.current_section)

    def set_current_section(self, section_name):
        if section_name in PythonBatchCommandAccum.section_order:
            self.current_section = section_name
            if self.current_section not in self.sections:
                self.sections[self.current_section] = Section(self.current_section)
        else:
            raise ValueError(f"{section_name} is not a known section_name name")

    def num_batch_commands(self):
        """ count recursively the number of batch commands - not including the top sections """
        counter = 0
        for a_section in self.sections.values():
            counter += a_section.num_sub_batch_commands()
        return counter

    def finalize_list_of_lines(self):
        lines = list()
        for section in PythonBatchCommandAccum.section_order:
            # config_vars["CURRENT_PHASE"] = section
            section_lines = self.instruction_lines[section]
            if section_lines:
                if section == "assign":
                    section_lines.sort()
                for section_line in section_lines:
                    resolved_line = config_vars.resolve_str_to_list(section_line)
                    lines.extend(resolved_line)
                lines.append("")  # empty string will cause to emit new line
        return lines

    def add(self, child_commands):
        assert not self.in_sub_accum, "PythonBatchCommandAccum.add: should not be called while sub_accum is in context"
        self.sections[self.current_section].add(child_commands)

    def _python_opening_code(self):
        instl_folder = pathlib.Path(__file__).joinpath(os.pardir, os.pardir).resolve()
        opening_code_lines = list()
        opening_code_lines.append(f"""# Creation time: {self.creation_time}""")
        opening_code_lines.append(f"""import sys""")
        opening_code_lines.append(f"""sys.path.append({utils.quoteme_raw_string(instl_folder)})""")
        opening_code_lines.append(f"""from pybatch import *""")
        opening_code_lines.append(f"""from configVar import config_vars""")
        PythonBatchCommandBase.total_progress = 0
        for section in self.sections.values():
            PythonBatchCommandBase.total_progress += section.num_progress_items()
        opening_code_lines.append(f"""PythonBatchCommandBase.total_progress = {PythonBatchCommandBase.total_progress}""")
        opening_code_lines.append(f"""PythonBatchCommandBase.running_progress = {PythonBatchCommandBase.running_progress}""")

        the_oc = "\n".join(opening_code_lines)
        the_oc += "\n\n"

        return the_oc

    def _python_closing_code(self):
        cc = f"\n# eof\n\n"
        return cc

    def __repr__(self):
        single_indent = "    "
        def create_unique_obj_name(obj):
            try:
                create_unique_obj_name.instance_counter += 1
            except AttributeError:
                create_unique_obj_name.instance_counter = 1
            obj_name = camel_to_snake_case(f"{obj.__class__.__name__}_{create_unique_obj_name.instance_counter:05}")
            return obj_name

        def _repr_helper(batch_items, io_str, indent):
            indent_str = single_indent*indent
            if isinstance(batch_items, list):
                for item in batch_items:
                    _repr_helper(item, io_str, indent)
            else:
                if batch_items.call__call__ is False and batch_items.is_context_manager is False:
                    io_str.write(f"""{indent_str}{repr(batch_items)}\n""")
                    _repr_helper(batch_items.child_batch_commands, io_str, indent)
                elif batch_items.call__call__ is False and batch_items.is_context_manager is True:
                    io_str.write(f"""{indent_str}with {repr(batch_items)}:\n""")
                    if batch_items.child_batch_commands:
                        _repr_helper(batch_items.child_batch_commands, io_str, indent+1)
                    else:
                        io_str.write(f"""{indent_str}{single_indent}pass\n""")
                elif batch_items.call__call__ is True and batch_items.is_context_manager is False:
                    io_str.write(f"""{indent_str}{repr(batch_items)}()\n""")
                    _repr_helper(batch_items.child_batch_commands, io_str, indent)
                elif batch_items.call__call__ is True and batch_items.is_context_manager is True:
                    obj_name = create_unique_obj_name(batch_items)
                    io_str.write(f"""{indent_str}with {repr(batch_items)} as {obj_name}:\n""")
                    io_str.write(f"""{indent_str}{single_indent}{obj_name}()\n""")
                    _repr_helper(batch_items.child_batch_commands, io_str, indent+1)

        prolog_str = io.StringIO()
        prolog_str.write(self._python_opening_code())
        if 'assign' in self.sections:
            _repr_helper(self.sections['assign'], prolog_str, 0)

        main_str = io.StringIO()
        the_command = config_vars.get("__MAIN_COMMAND__", "woolly mammoth")
        runtimer = PythonBatchRuntime(the_command)
        for section_name in PythonBatchCommandAccum.section_order:
            if section_name in self.sections:
                if section_name != 'assign':
                    runtimer += self.sections[section_name]
        main_str.write("\n")
        _repr_helper(runtimer, main_str, 0)
        main_str.write("\n")
        main_str.write(self._python_closing_code())

        main_str_resolved = config_vars.resolve_str(main_str.getvalue())
        main_str_resolved = config_vars.replace_unresolved_with_native_var_pattern(main_str_resolved, list(config_vars["__CURRENT_OS_NAMES__"])[0])

        the_whole_repr = prolog_str.getvalue()+main_str_resolved
        return the_whole_repr

    def repr_batch_win(self):
        def _repr_helper(batch_items, io_str):
            if isinstance(batch_items, list):
                for item in batch_items:
                    _repr_helper(item, io_str)
                    _repr_helper(item.child_batch_commands, io_str)
            else:
                io_str.write(batch_repr(batch_items))
        PythonBatchCommandBase.total_progress = 0
        io_str = io.StringIO()
        #io_str.write(self._python_opening_code())
        #_repr_helper(self.context_stack[0], io_str)
        #io_str.write(self._python_closing_code())
        return io_str.getvalue()

    def repr_batch_mac(self):
        return ""

    def progress_msg_self(self):
        """ """
        return ""

    def __call__(self, *args, **kwargs):
        pass
