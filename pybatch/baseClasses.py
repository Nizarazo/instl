import os
import sys
import abc
from typing import Dict, List
import time
from contextlib import contextmanager
from typing import List
import logging
log = logging.getLogger()

import utils


class PythonBatchCommandBase(abc.ABC):
    """ PythonBatchCommandBase is the base class for all classes implementing batch commands.
        PythonBatchCommandBase implement context manager interface:
        __enter__: will print progress message (if needed)
                    derived classes should not override __enter__ and should not do any actual work here but implement
                    the work in __call__. If something must be done in __enter__ override enter_self
        __exit__: will handle exceptions and print warning/error messages, or ignore errors if needed
                 derived classes should not override __exit__. If something must be done in __exit__ override exit_self
        Derived classes must implement some additional methods:
        __repr__: must be implemented correctly so the returned string can be passed to eval to recreate the object
        __init__: must record all parameters needed to implement __repr__ and must not do any actual work!
        __call__: here the real work is done (if any)

        members:
        self.doing - the most possible detailed description of what the object is doing. Derived classes should update this member
            during operations, e.g. if a folder is copied file by file, self.doing will be rewritten as each file is copied.
                self.doing is often very similar to what is returned by progress_msg_self, however progress_msg_self is description
                of what was *asked* to be done, while doing is meant to describe what was actually being done when an error occurred.

        non_representative__dict__keys - list of keys of self.__dict__ that should not be used when comparing or displaying self
    """
    stage_stack = list()
    instance_counter: int = 0
    total_progress: int = 0
    running_progress: int = 0
    essential = True
    call__call__: bool = True         # when false no need to call
    is_context_manager: bool = True   # when true need to be created as context manager
    is_anonymous: bool = False        # anonymous means the object is just a container for child_batch_commands and should not be used by itself

    kwargs_defaults = {'own_progress_count': 1,
                       'report_own_progress': True,
                       'ignore_all_errors': False,
                       'remark': None,
                       'recursive': False,
                       "reply_config_var": None}

    @classmethod
    def __init_subclass__(cls, essential=True, call__call__=True, is_context_manager=True, is_anonymous=False, kwargs_defaults=None, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.essential = essential
        cls.call__call__ = call__call__
        cls.is_context_manager = is_context_manager
        cls.is_anonymous = is_anonymous

        parent_kwargs_defaults = {}
        if hasattr(cls, "kwargs_defaults"):
            parent_kwargs_defaults.update(cls.kwargs_defaults)

        # create a new, unique kwargs_defaults for the class, that will override the parent class' kwargs_defaults. To keep the values from parent class create a copy named parent_kwargs_defaults.
        # Beware, simply doing cls.kwargs_defaults.update(parent_kwargs_defaults) will update the parent class kwargs_defaults, and this will effect other classes inheriting from that base
        cls.kwargs_defaults = parent_kwargs_defaults
        if kwargs_defaults:
            cls.kwargs_defaults.update(kwargs_defaults)

        #print(f"{cls.__name__}: {parent_kwargs_defaults}/{cls.kwargs_defaults}")

    @abc.abstractmethod
    def __init__(self, **kwargs):
        PythonBatchCommandBase.instance_counter += 1

        for kwarg_name, kwarg_default_value in self.kwargs_defaults.items():
            kwarg_value = kwargs.get(kwarg_name, kwarg_default_value)
            setattr(self, kwarg_name, kwarg_value)

        self.exceptions_to_ignore = []
        self.child_batch_commands = []
        self.enter_time = None
        self.exit_time = None
        self.in_sub_accum = False
        self.essential_action_counter = 0
        self._error_dict = None
        self.doing = None  # description of what the object is doing, derived classes should update this member during operations
        self.current_working_dir = None
        self.non_representative__dict__keys = ['remark', 'enter_time', 'exit_time', 'non_representative__dict__keys', 'progress', '_error_dict', "doing", 'exceptions_to_ignore', '_get_ignored_files_func', 'last_src', 'last_dst', 'last_step', 'current_working_dir']

    def repr_default_kwargs(self, all_args):
        """ get a text representation of the __init__(kwargs) for a sub class.
            returns a list of text values in the form "x=y". args that
            are listed in self.non_representative__dict__keys will not be included
            also e
        """
        for kwarg_name, kwarg_default_value in sorted(self.kwargs_defaults.items()):
            if kwarg_name not in self.non_representative__dict__keys:
                current_value = getattr(self, kwarg_name, kwarg_default_value)
                if current_value != kwarg_default_value:
                    all_args.append(f"""{kwarg_name}={utils.quoteme_raw_by_type(current_value)}""")

    #@abc.abstractmethod
    def repr_own_args(self, all_args: List[str]) -> None:
        pass

    def __repr__(self) -> str:
        all_args = list()
        self.repr_own_args(all_args)
        self.repr_default_kwargs(all_args)
        all_args = list(filter(lambda x: x is not None, all_args))
        the_repr = f"{self.__class__.__name__}("
        the_repr += ", ".join(all_args)
        the_repr += ")"

        return the_repr

    def __str__(self):
        return f"{self.__class__.__name__} {PythonBatchCommandBase.instance_counter}"

    @classmethod
    def set_a_kwargs_default(cls, default_name, new_default_value):
        cls.kwargs_defaults[default_name] = new_default_value

    def stage_str(self) -> str:
        return ""

    def major_stage_str(self) -> str:
        """ return the top most stage name in PythonBatchCommandBase.stage_stack that is not None or empty
            if PythonBatchCommandBase.stage_stack is empty return the class name
        """
        for stage in PythonBatchCommandBase.stage_stack:
            retVal = stage.stage_str()
            if retVal:
                break
        else:
            retVal = self.__class__.__name__
        return retVal

    @abc.abstractmethod
    def progress_msg_self(self) -> str:
        """ classes overriding PythonBatchCommandBase should add their own progress message
        """
        return f"{self.__class__.__name__}"

    def error_dict_self(self, exc_type, exc_val, exc_tb) -> None:
        pass

    @abc.abstractmethod
    def __call__(self, *args, **kwargs):
        pass

    def unnamed__init__param(self, value):
        value_str = utils.quoteme_raw_if_string(value)
        return value_str

    def named__init__param(self, name, value):
        value_str = utils.quoteme_raw_if_string(value)
        param_repr = f"{name}={value_str}"
        return param_repr

    def optional_named__init__param(self, name, value, default=None):
        param_repr = None
        if value != default:
            value_str = utils.quoteme_raw_if_list(value)
            param_repr = f"{name}={value_str}"
        return param_repr

    def total_progress_count(self) -> int:
        retVal = self.own_progress_count
        for sub in self.child_batch_commands:
            retVal += sub.total_progress_count()
        return retVal

    def is_essential(self) -> bool:
        retVal = self.essential
        if not retVal:
            retVal = any([child.is_essential() for child in self.child_batch_commands])
        return retVal

    def sub_commands(self) -> List:
        return self.child_batch_commands

    def __iadd__(self, child_commands):
        self.add(child_commands)
        return self

    def add(self, instructions):
        assert not self.in_sub_accum, "PythonBatchCommandAccum.add: should not be called while sub_accum is in context"
        if isinstance(instructions, PythonBatchCommandBase):
            if instructions.is_anonymous:  # no need for the parent, just the children
                self.child_batch_commands.extend(instructions.child_batch_commands)
            else:
                self.child_batch_commands.append(instructions)
        else:
            for instruction in instructions:
                self.add(instruction)

    @contextmanager
    def sub_accum(self, context):
        assert not self.in_sub_accum, "PythonBatchCommandAccum.sub_accum: should not be called while another sub_accum is in context"
        self.in_sub_accum = True
        yield context
        self.in_sub_accum = False
        if context.is_essential():
            self.add(context)

    def representative_dict(self):
        """  return a partial self.__dict__ without keys tha should not be used for presentation or comparing"""
        return {k: self.__dict__[k] for k in self.__dict__.keys() if k not in self.non_representative__dict__keys}

    def __eq__(self, other) -> bool:
        my_repr_dict = self.representative_dict()
        other_repr_dict = other.representative_dict()
        is_eq =  my_repr_dict == other_repr_dict
        return is_eq

    def explain_diff(self, other) -> str:
        retVal = list()
        my_repr_dict = self.representative_dict()
        other_repr_dict = other.representative_dict()
        for my_key in my_repr_dict:
            if my_key not in other_repr_dict:
                retVal.append(f"{my_key} in 1st but not in 2nd")
            elif my_repr_dict[my_key] != other_repr_dict[my_key]:
                retVal.append(f"1st[{my_key}]({my_repr_dict[my_key]}) != 2nd[{my_key}]({other_repr_dict[my_key]})")

        for other_key in other_repr_dict:
            if other_key not in my_repr_dict:
                retVal.append(f"{my_key} in 2nd but not in 1st")
        return ", ".join(retVal)

    def __hash__(self):
        the_hash = hash(tuple(sorted(self.__dict__.items())))
        return the_hash

    def progress_msg(self) -> str:
        the_progress_msg = f"Progress {PythonBatchCommandBase.running_progress} of {PythonBatchCommandBase.total_progress};"
        return the_progress_msg

    def warning_msg_self(self) -> str:
        """ classes overriding PythonBatchCommandBase can add their own warning message
        """
        return f"{self.__class__.__name__}"

    def enter_self(self) -> None:
        """ classes overriding PythonBatchCommandBase can add code here without
            repeating __enter__, bit not do any actual work!
        """
        pass

    def error_dict(self, exc_type, exc_val, exc_tb) -> Dict:
        if self._error_dict is None:
            self._error_dict = dict()
        self.error_dict_self(exc_type, exc_val, exc_tb)
        if not self.doing:
            self.doing = self.progress_msg_self()
        self._error_dict.update({
            'doing': self.doing,
            'major_stage': self.major_stage_str(),
            'stage': ".".join(filter(None, (stage.stage_str() for stage in PythonBatchCommandBase.stage_stack))),
            'instl_class': repr(self),
            'obj__dict__': self.representative_dict(),
            'local_time': time.strftime("%Y-%m-%d_%H.%M.%S"),
            'progress_counter': PythonBatchCommandBase.running_progress,
            'current_working_dir': self.current_working_dir,
             })
        if exc_val:
            self._error_dict.update({
                'exception_type': str(type(exc_val).__name__),
                'exception_str': str(exc_val),
                })
        if exc_tb:
            self._error_dict.update({
                "batch_file": exc_tb.tb_frame.f_code.co_filename,
                "batch_line": exc_tb.tb_lineno
                })
        return self._error_dict

    def __enter__(self):
        PythonBatchCommandBase.stage_stack.append(self)
        self.enter_time = time.perf_counter()
        try:
            PythonBatchCommandBase.running_progress += self.own_progress_count
            if self.report_own_progress:
                log.info(f"{self.progress_msg()} {self.progress_msg_self()}")
                if PythonBatchCommandBase.running_progress > PythonBatchCommandBase.total_progress:
                    log.warning(f"running_progress ({PythonBatchCommandBase.running_progress}) > total_progress ({PythonBatchCommandBase.total_progress})")
            self.current_working_dir =  os.getcwd()
            self.enter_self()
        except Exception as ex:
            suppress_exception = self.__exit__(*sys.exc_info())
            if not suppress_exception:
                raise
        return self

    def exit_self(self, exit_return) -> None:
        """ classes overriding PythonBatchCommandBase can add code here without
            repeating __exit__.
            exit_self will be called regardless of exceptions
            param exit_return is what __exit__ will return
        """
        pass

    def should_ignore__exit__exception(self, exc_type, exc_val, exc_tb):
        """ child classes can override for finer control on what to ignore"""
        retVal = exc_type in self.exceptions_to_ignore
        return retVal

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit_time = time.perf_counter()
        suppress_exception = False
        if exc_type is None or self.ignore_all_errors:
            suppress_exception = True
        elif self.should_ignore__exit__exception(exc_type, exc_val, exc_tb):
            self.log_result(logging.WARNING, self.warning_msg_self(), exc_val)
            suppress_exception = True
        else:
            if not hasattr(exc_val, "raising_obj"):
                setattr(exc_val, "raising_obj", self)
        self.exit_self(exit_return=suppress_exception)
        if suppress_exception:
            PythonBatchCommandBase.stage_stack.pop()
        command_time_ms = (self.exit_time-self.enter_time)*1000.0
        #log.debug(f"{self.progress_msg()} time: {command_time_ms:.2f}ms")
        return suppress_exception

    def log_result(self, log_lvl, message, exc_val):
        log.log(log_lvl, f"{self.progress_msg()} {message}; {exc_val.__class__.__name__}: {exc_val}")
