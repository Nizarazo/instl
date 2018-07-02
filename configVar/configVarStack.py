#!/usr/bin/env python3


"""
    Copyright (c) 2012, Shai Shasag
    All rights reserved.
    Licensed under BSD 3 clause license, see LICENSE file for details.

    configVarList module has but a single class ConfigVarList
    import pyinstl.configVarList
"""

import os
from contextlib import contextmanager

#sys.path.append(os.path.realpath(os.path.join(__file__, "..", "..")))

import utils
import aYaml
from . import configVarList
from . import configVarOne


class ConfigVarStack(configVarList.ConfigVarList):
    """ Keeps a list of named build config values.
        Help values resolve $() style references. """

    def __init__(self):
        super().__init__()
        self._ConfigVarList_objs = list()
        self.push_scope()

    #def __len__(self):
    #    """ return number of ConfigVars """
    #    return len(self._ConfigVarList_objs)

    def __getitem__(self, var_name):
        """ return a ConfigVar object by it's name """
        for level_var_list in reversed(self._ConfigVarList_objs):
            if var_name in level_var_list:
                return level_var_list[var_name]
        raise KeyError

    #def __delitem__(self, key):
    #    """ remove a ConfigVar object by it's name """
    #    if key in self._ConfigVarList_objs:
    #        del self._ConfigVarList_objs[key]

    def __iter__(self):
        return iter(list(self.keys()))

    def __reversed__(self):
        return reversed(list(self.keys()))

    def __contains__(self, var_name):
        for level_var_list in self._ConfigVarList_objs:
            if var_name in level_var_list:
                return True
        return False

    def keys(self):
        the_keys = utils.unique_list()
        for a_var_list in reversed(self._ConfigVarList_objs):
            the_keys.extend(list(a_var_list.keys()))
        return list(the_keys)

    def get_configVar_obj(self, var_name):
        try:
            retVal = self[var_name]
        except KeyError:
            retVal = self._ConfigVarList_objs[-1].get_configVar_obj(var_name)
        return retVal

    def set_value_if_var_does_not_exist(self, var_name, var_value, description=None):
        """ If variable does not exist it will be created and assigned the new value.
            Otherwise variable will remain as is. Good for setting defaults to variables
            that were not read from file.
        """
        try:
            self[var_name]
        except KeyError:
            new_var = self._ConfigVarList_objs[-1].get_configVar_obj(var_name)
            new_var.append(var_value)
            if description is not None:
                new_var.description = description

    def add_const_config_variable(self, var_name, description="", *values):
        """ add a const single value object """
        try:
            values_as_strs = list(map(str, values))
            var_obj = self[var_name]
            if var_name.endswith(configVarOne.ConfigVar.variable_name_endings_to_normpath):
                values_as_strs = [os.path.normpath(value) for value in values_as_strs]
            if list(var_obj) != values_as_strs:
                raise Exception("Const variable {} ({}) already defined: new values: {}"\
                                ", previous values: {}".format(var_name, self.get_configVar_obj(var_name).description,
                                                               str(values), str(list(self.get_configVar_obj(var_name)))))
        except KeyError:
            # noinspection PyUnboundLocalVariable
            self._ConfigVarList_objs[-1].add_const_config_variable(var_name, description, *values_as_strs)

    def repr_var_for_yaml(self, var_name, include_comments=True, resolve=True):
        the_comment = None
        if include_comments:
            the_comment = self[var_name].description
        if resolve:
            var_value = self.ResolveVarToList(var_name)
        else:
            var_value = self.unresolved_var_to_list(var_name)
        if len(var_value) == 1:
            var_value = var_value[0]
        retVal = aYaml.YamlDumpWrap(var_value, comment=the_comment)
        return retVal

    def repr_for_yaml(self, which_vars=None, include_comments=True, resolve=True, ignore_unknown_vars=False):
        retVal = dict()
        vars_list = list()
        if not which_vars:
            vars_list.extend(list(self.keys()))
        elif isinstance(which_vars, str):
            vars_list.append(which_vars)
        else:
            vars_list = which_vars
        if not hasattr(vars_list, '__iter__'):  # if which_vars is a list
            ValueError("ConfigVarList.repr_for_yaml can except string, list or None, not "+type(which_vars)+" "+str(which_vars))
        theComment = ""
        for var_name in vars_list:
            if var_name in self:
                 retVal[var_name] = self.repr_var_for_yaml(var_name, include_comments=include_comments, resolve=resolve)
            elif not ignore_unknown_vars:
                retVal[var_name] = aYaml.YamlDumpWrap(value="UNKNOWN VARIABLE", comment=var_name+" is not in variable list")
        return retVal

    def push_scope(self, scope=None):
        #Not moved to new ConfigVarStack#
        if scope is None:
            scope = configVarList.ConfigVarList()
        if not isinstance(scope, configVarList.ConfigVarList):
            raise TypeError("scope must be of type ConfigVarList")
        self._ConfigVarList_objs.append(scope)

    def pop_scope(self):
        #Not moved to new ConfigVarStack#
        retVal = self._ConfigVarList_objs.pop()
        return retVal

    @contextmanager
    def push_scope_context(self, scope=None):
        #moved to new ConfigVarStack#
        self.push_scope(scope)
        yield self
        self.pop_scope()

    def freeze_vars_on_first_resolve(self):
        for var_obj in self._ConfigVarList_objs:
            var_obj.freeze_vars_on_first_resolve()

    def print_statistics(self):
        if self.ResolveVarToBool("PRINT_STATISTICS"):
            total_non_freeze = 0
            total_with_freeze = 0
            max_non_freeze_var = None
            max_with_freeze_var = None
            all_vars = set(list(self.resolve_with_freeze_statistics.keys()) + list(self.resolve_non_freeze_statistics.keys()))
            for var in sorted(all_vars):
                non_freeze_count = self.resolve_non_freeze_statistics.get(var, 0)
                total_non_freeze += non_freeze_count
                if non_freeze_count > self.resolve_non_freeze_statistics.get(max_non_freeze_var, 0):
                    max_non_freeze_var = var
                with_freeze_count = self.resolve_with_freeze_statistics.get(var, 0)
                total_with_freeze += with_freeze_count
                if with_freeze_count > self.resolve_with_freeze_statistics.get(max_with_freeze_var, 0):
                    max_with_freeze_var = var
                print(var, non_freeze_count, with_freeze_count)
            print("max non freeze", max_non_freeze_var, self.resolve_non_freeze_statistics.get(max_non_freeze_var, 0))
            print("max with freeze", max_with_freeze_var, self.resolve_with_freeze_statistics.get(max_with_freeze_var, 0))
            print("total non freeze", total_non_freeze)
            print("total with freeze", total_with_freeze)

# This is the global variable list serving all parts of instl
var_stack = ConfigVarStack()
