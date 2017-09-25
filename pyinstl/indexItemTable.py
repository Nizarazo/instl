#!/usr/bin/env python3


import os
from collections import OrderedDict

from sqlalchemy.ext import baked
from sqlalchemy import bindparam
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db_alchemy import create_session,\
    IndexItemDetailOperatingSystem, \
    IndexItemRow, \
    IndexItemDetailRow, \
    IndexRequireTranslate, \
    FoundOnDiskItemRow, \
    ConfigVar, get_engine

import svnTree  # do not remove must be here before IndexItemsTable.execute_script is called
import utils
from configVar import var_stack

# todo: these were copied from the late Install Item.py and should find a better home
os_names = ('common', 'Mac', 'Mac32', 'Mac64', 'Win', 'Win32', 'Win64')
allowed_item_keys = ('name', 'guid','install_sources', 'install_folders', 'inherit',
                     'depends', 'actions', 'remark', 'version', 'phantom_version',
                     'direct_sync', 'previous_sources', 'info_map')
allowed_top_level_keys = os_names[1:] + allowed_item_keys
action_types = ('pre_copy', 'pre_copy_to_folder', 'pre_copy_item',
                'post_copy_item', 'post_copy_to_folder', 'post_copy',
                'pre_remove', 'pre_remove_from_folder', 'pre_remove_item',
                'remove_item', 'post_remove_item', 'post_remove_from_folder',
                'post_remove', 'pre_doit', 'doit', 'post_doit')
file_types = ('!dir_cont', '!file', '!dir')


class IndexItemsTable(object):
    os_names_to_num = OrderedDict([('common', 0), ('Mac', 1), ('Mac32', 2), ('Mac64', 3), ('Win', 4), ('Win32', 5), ('Win64', 6)])
    install_status = {"none": 0, "main": 1, "update": 2, "depend": 3, "remove": -1}
    action_types = ('pre_copy', 'pre_copy_to_folder', 'pre_copy_item',
                    'post_copy_item', 'post_copy_to_folder', 'post_copy',
                    'pre_remove', 'pre_remove_from_folder', 'pre_remove_item',
                    'remove_item', 'post_remove_item', 'post_remove_from_folder',
                    'post_remove', 'pre_doit', 'doit', 'post_doit')
    not_inherit_details = ("name", "inherit")

    def __init__(self):
        self.session = create_session()
        self.clear_tables()
        self.os_names_db_objs = list()
        self.add_default_values()
        self.add_triggers()
        self.add_views()
        self.commit_changes()
        # inspector = reflection.Inspector.from_engine(get_engine())
        # print("Tables:", inspector.get_table_names())
        # print("Views:", inspector.get_view_names())
        self.baked_queries_map = dict()
        self.bakery = baked.bakery()
        self.locked_tables = set()

    def __del__(self):
        self.unlock_all_tables()

    def get_db_url(self):
        return self.session.bind.url

    def commit_changes(self):
        self.session.commit()

    def clear_tables(self):
        # print(get_engine().table_names())
        self.drop_triggers()
        self.drop_views()
        self.session.query(IndexRequireTranslate).delete()
        self.session.query(IndexItemDetailOperatingSystem).delete()
        self.session.query(IndexItemDetailRow).delete()
        self.session.query(IndexItemRow).delete()
        self.commit_changes()

    def add_default_values(self):

        for os_name, _id in IndexItemsTable.os_names_to_num.items():
            new_item = IndexItemDetailOperatingSystem(_id=_id, name=os_name, os_is_active=False)
            self.os_names_db_objs.append(new_item)
        self.session.add_all(self.os_names_db_objs)

    def execute_script(self, script_text):
        db_conn = get_engine().raw_connection()
        db_curs = db_conn.cursor()
        db_curs.executescript(script_text)
        db_curs.close()
        db_conn.close()

    def execute_script_from_defaults(self, script_file_name):
        script_file_path = os.path.join(var_stack.ResolveVarToStr("__INSTL_DATA_FOLDER__"), "defaults", script_file_name)
        with open(script_file_path, "r") as rfd:
            self.execute_script(rfd.read())

    def add_triggers(self):
        self.execute_script_from_defaults("create-triggers.sql")

    def drop_triggers(self):
        self.execute_script_from_defaults("drop-triggers.sql")

    def add_views(self):
        self.execute_script_from_defaults("create-views.sql")

    def drop_views(self):
        self.execute_script_from_defaults("drop-views.sql")

    def lock_table(self, table_name):
        query_text = """
            CREATE TRIGGER IF NOT EXISTS lock_INSERT_{table_name}
            BEFORE INSERT ON {table_name}
            BEGIN
                SELECT raise(abort, '{table_name} is locked no INSERTs');
            END;
            CREATE TRIGGER IF NOT EXISTS lock_UPDATE_{table_name}
            BEFORE UPDATE ON {table_name}
            BEGIN
                SELECT raise(abort, '{table_name} is locked no UPDATEs');
            END;
            CREATE TRIGGER IF NOT EXISTS lock_DELETE_{table_name}
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT raise(abort, '{table_name} is locked no DELETEs');
            END;
        """.format(table_name=table_name)
        self.execute_script(query_text)
        self.commit_changes()
        self.locked_tables.add(table_name)

    def unlock_table(self, table_name):
        query_text = """
            DROP TRIGGER IF EXISTS lock_INSERT_{table_name};
            DROP TRIGGER IF EXISTS lock_UPDATE_{table_name};
            DROP TRIGGER IF EXISTS lock_DELETE_{table_name};
        """.format(table_name=table_name)
        self.execute_script(query_text)
        self.commit_changes()
        self.locked_tables.remove(table_name)
        print("unlocked", table_name)

    def unlock_all_tables(self):
        for table_name in list(self.locked_tables):
            self.unlock_table(table_name)

    def activate_all_oses(self):
        """ adds all known os names to the list of os that will influence all get functions
            such as depend_list, source_list etc.
            This method is useful in code that does reporting or analyzing, where
            there is need to have access to all oses not just the current or target os.
        """
        query_text = """
            UPDATE IndexItemDetailOperatingSystem
            SET os_is_active = 1
         """
        try:
            exec_result = self.session.execute(query_text)
            self.commit_changes()
        except SQLAlchemyError as ex:
            print(ex)
            raise

    def reset_active_oses(self):
        """ resets the list of os that will influence all get functions
            such as depend_list, source_list etc.
            This method is useful in code that does reporting or analyzing, where
            there is need to have access to all oses not just the current or target os.
        """
        self.activate_specific_oses()

    def activate_specific_oses(self, *for_oses):
        """ adds another os name to the list of os that will influence all get functions
            such as depend_list, source_list etc.
        """
        for_oses = *for_oses, "common"
        quoted_os_names = [utils.quoteme_double(os_name) for os_name in for_oses]
        query_vars = ", ".join(quoted_os_names)
        query_text = """
            UPDATE IndexItemDetailOperatingSystem
            SET os_is_active = CASE WHEN IndexItemDetailOperatingSystem.name IN ({0}) THEN
                    1
                ELSE
                    0
                END;
        """.format(query_vars)
        try:
            exec_result = self.session.execute(query_text)
            self.commit_changes()
        except SQLAlchemyError as ex:
            print(ex)
            raise

    def get_active_oses(self):
        retVal = list()
        query_text = """
        SELECT name, os_is_active
        FROM IndexItemDetailOperatingSystem
        ORDER BY _id
        """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal

    def insert_require_to_db(self, require_items):
        for iid, details in require_items.items():
            old_item = self.get_index_item(iid)
            if old_item is not None:
                old_item.from_require = True
                self.session.add_all(details)
            else:
                print(iid, "found in require but not in index")
        self.commit_changes()

    def get_all_require_translate_items(self):
        """
        """
        if "get_all_require_translate_items" not in self.baked_queries_map:
            the_query = self.bakery(lambda session: session.query(IndexRequireTranslate))
            the_query += lambda q: q.order_by(IndexRequireTranslate.iid)
            self.baked_queries_map["get_all_require_translate_items"] = the_query
        else:
            the_query = self.baked_queries_map["get_all_require_translate_items"]
        retVal = the_query(self.session).all()
        return retVal

    def get_all_index_items(self):
        """
        tested by: TestItemTable.test_??_IndexItemRow_get_item, test_??_empty_tables
        :return: list of all IndexItemRow objects in the db, empty list if none are found
        """
        if "get_all_items" not in self.baked_queries_map:
            the_query = self.bakery(lambda session: session.query(IndexItemRow))
            the_query += lambda q: q.order_by(IndexItemRow.iid)
            self.baked_queries_map["get_all_items"] = the_query
        else:
            the_query = self.baked_queries_map["get_all_items"]
        retVal = the_query(self.session).all()
        return retVal

    def get_index_item(self, iid_to_get):
        """
        tested by: TestItemTable.test_ItemRow_get_item
        :return: IndexItemRow object matching iid_to_get or None
        """
        if "get_index_item" not in self.baked_queries_map:
            the_query = self.bakery(lambda q: q.query(IndexItemRow))
            the_query += lambda q: q.filter(IndexItemRow.iid == bindparam("iid_to_get"))
            self.baked_queries_map["get_index_item"] = the_query
        else:
            the_query = self.baked_queries_map["get_index_item"]
        retVal = the_query(self.session).params(iid_to_get=iid_to_get).first()
        return retVal

    def get_all_iids_with_guids(self):
        """
        :return: list of all iids in the db that have guids, empty list if none are found
        """
        retVal = list()
        query_text = """
            SELECT owner_iid
            from IndexItemDetailRow
            WHERE IndexItemDetailRow.detail_name="guid"
            AND owner_iid=original_iid
            ORDER BY owner_iid
            """

        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal = exec_result.fetchall()
                retVal = [mm[0] for mm in retVal]
        except SQLAlchemyError as ex:
            raise

        return retVal

    def get_all_installed_iids(self):
        """
        :return: list of all iids in the db that have guids, empty list if none are found
        """
        if "get_all_installed_iids" not in self.baked_queries_map:
            the_query = self.bakery(lambda q: q.query(IndexItemDetailRow.original_iid))
            the_query += lambda q: q.filter(IndexItemDetailRow.detail_name == "require_by",
                                            IndexItemDetailRow.detail_value == IndexItemDetailRow.original_iid, IndexItemDetailRow.os_is_active == True)
            self.baked_queries_map["get_all_installed_iids"] = the_query
        else:
            the_query = self.baked_queries_map["get_all_installed_iids"]
        retVal = the_query(self.session).all()
        retVal = [m[0] for m in retVal]
        return retVal

    def get_all_installed_iids_needing_update(self):
        """ Return all iids that were installed, have a version, and that version is different from the version in the index
        """
        retVal = list()
        query_text = """
                SELECT DISTINCT require_version.owner_iid, require_version.detail_value AS require, remote_version.detail_value AS remote
                FROM IndexItemDetailRow AS require_version
                LEFT JOIN (
                    SELECT owner_iid, detail_value, min(generation)
                    from IndexItemDetailRow AS remote_version
                    WHERE detail_name="version"
                    AND os_is_active = 1
                    GROUP BY owner_iid
                    ) remote_version
                WHERE detail_name="require_version"
                      AND remote_version.owner_iid=require_version.owner_iid
                      AND require_version.detail_value!=remote_version.detail_value
                      AND require_version.os_is_active = 1
                GROUP BY require_version.owner_iid
            """
            # "GROUP BY" will make sure only one row is returned for an iid.
            # multiple rows can be found if and IID has 2 previous_sources both were found
            # on disk and their version identified.
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal = exec_result.fetchall()   # now retVal is a list of (IID, require_ver, remote_ver)
                retVal = [mm[0] for mm in retVal] # need only the IID, but getting the versions is for debugging
        except SQLAlchemyError as ex:
            raise

        return retVal

    def get_all_iids(self):
        """
        tested by: TestItemTable.test_06_get_all_iids
        :return: list of all iids in the db, empty list if none are found
        """
        retVal = list()
        query_text = """
          SELECT iid FROM IndexItemRow
          ORDER BY iid
        """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal = exec_result.fetchall()
                retVal = [mm[0] for mm in retVal]
        except SQLAlchemyError as ex:
            raise
        return retVal

    def create_default_index_items(self, iids_to_ignore):
        the_os_id = self.os_names_to_num['common']
        the_iid = "__ALL_ITEMS_IID__"
        all_items_item = IndexItemRow(iid=the_iid, inherit_resolved=True, from_index=False, from_require=False)
        self.session.add(all_items_item)
        for iid in self.get_all_iids():
            if iid not in iids_to_ignore:
                depends_details = IndexItemDetailRow(original_iid=the_iid,
                                                 owner_iid=the_iid,
                                                 detail_name='depends',
                                                 detail_value=iid,
                                                 os_id=the_os_id,
                                                 generation=0)
                self.session.add(depends_details)

        the_iid = "__ALL_GUIDS_IID__"
        all_guids_item = IndexItemRow(iid=the_iid, inherit_resolved=True, from_index=False, from_require=False)
        self.session.add(all_guids_item)
        all_iids_with_guids = self.get_all_iids_with_guids()
        for iid in all_iids_with_guids:
            if iid not in iids_to_ignore:
                depends_details = IndexItemDetailRow(original_iid=the_iid,
                                                 owner_iid=the_iid,
                                                 detail_name='depends',
                                                 detail_value=iid,
                                                 os_id=the_os_id,
                                                 generation=0)
                self.session.add(depends_details)
        self.commit_changes()

    def create_default_require_items(self, iids_to_ignore):
        the_os_id = self.os_names_to_num['common']
        the_iid = "__REPAIR_INSTALLED_ITEMS__"
        repair_item = IndexItemRow(iid=the_iid, inherit_resolved=True, from_index=False, from_require=False)
        self.session.add(repair_item)
        for iid in self.get_all_installed_iids():
            if iid not in iids_to_ignore:
                repair_item_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid,
                                                  detail_name='depends', detail_value=iid,
                                                  os_id=the_os_id, generation=0)
                self.session.add(repair_item_detail)

        the_iid = "__UPDATE_INSTALLED_ITEMS__"
        update_item = IndexItemRow(iid=the_iid, inherit_resolved=True, from_index=False, from_require=False)
        self.session.add(update_item)
        iids_needing_update = self.get_all_installed_iids_needing_update()
        for iid in iids_needing_update:
            if iid not in iids_to_ignore:
                update_item_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid,
                                                  detail_name='depends', detail_value=iid,
                                                  os_id=the_os_id, generation=0)
                self.session.add(update_item_detail)
        self.commit_changes()

    def get_item_by_resolve_status(self, iid_to_get, resolve_status):  # tested by: TestItemTable.test_get_item_by_resolve_status
        # http://stackoverflow.com/questions/29161730/what-is-the-difference-between-one-and-first
        if "get_item_by_resolve_status" not in self.baked_queries_map:
            the_query = self.bakery(lambda q: q.query(IndexItemRow))
            the_query += lambda  q: q.filter(IndexItemRow.iid == bindparam("_iid"),
                                             IndexItemRow.inherit_resolved == bindparam("_resolved"))
            self.baked_queries_map["get_item_by_resolve_status"] = the_query
        else:
            the_query = self.baked_queries_map["get_item_by_resolve_status"]
        retVal = the_query(self.session).params(_iid=iid_to_get, _resolved=resolve_status).first()
        return retVal

    def get_items_by_resolve_status(self, resolve_status):  # tested by: TestItemTable.test_get_items_by_resolve_status
        if "get_items_by_resolve_status" not in self.baked_queries_map:
            the_query = self.bakery(lambda q: q.query(IndexItemRow))
            the_query += lambda  q: q.filter(IndexItemRow.inherit_resolved == bindparam("_resolved"))
            the_query += lambda q: q.order_by(IndexItemRow.iid)
            self.baked_queries_map["get_items_by_resolve_status"] = the_query
        else:
            the_query = self.baked_queries_map["get_items_by_resolve_status"]
        retVal = the_query(self.session).params(_resolved=resolve_status).all()
        return retVal

    def get_original_details_values_for_active_iid(self, iid, detail_name, unique_values=False):
        """ get the items's original (e.g. not inherited) values for a specific detail
            for specific iid - but only if detail is in active os
        """
        retVal = list()
        distinct = "DISTINCT" if unique_values else ""
        query_text = """
            SELECT {distinct} detail_value
            FROM IndexItemDetailRow
            WHERE original_iid = :iid
            AND detail_name = :detail_name
            AND os_is_active = 1
            ORDER BY _id
        """.format(distinct=distinct)
        try:
            exec_result = self.session.execute(query_text, {'iid': iid, 'detail_name': detail_name})
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal = [mm[0] for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_original_details(self, iid=None, detail_name=None, in_os=None):
        """
        tested by: TestItemTable.test_get_original_details_* functions
        :param iid: get detail for specific iid or all if None
        :param detail_name: get detail with specific name or all names if None
        :param in_os: get detail for os name or for all oses if None
        :return: list original details in the order they were inserted
        """
        if "get_original_details" not in self.baked_queries_map:
            the_query = self.bakery(lambda session: session.query(IndexItemDetailRow))
            the_query += lambda q: q.filter(IndexItemDetailRow.original_iid.like(bindparam('iid')))
            the_query += lambda q: q.filter(IndexItemDetailRow.detail_name.like(bindparam('detail_name')))
            the_query += lambda q: q.filter(IndexItemDetailRow.os_id.like(bindparam('in_os')))
            the_query += lambda q: q.order_by(IndexItemDetailRow._id)
            self.baked_queries_map["get_original_details"] = the_query
        else:
            the_query = self.baked_queries_map["get_original_details"]

        # params with None are turned to '%'
        params = [iid, detail_name, in_os]
        for iparam in range(len(params)):
            if params[iparam] is None: params[iparam] = '%'
        retVal = the_query(self.session).params(iid=params[0], detail_name=params[1], in_os=params[2]).all()
        return retVal

    def get_resolved_details_for_active_iid(self, iid, detail_name=None):
        """ get the original and inherited IndexItemDetailRow's for a specific detail
            for specific iid - but only if detail is in active os
        """
        if "get_resolved_details_for_active_iid" not in self.baked_queries_map:
            the_query = self.bakery(lambda session: session.query(IndexItemDetailRow))
            the_query += lambda q: q.filter(IndexItemDetailRow.owner_iid == bindparam('iid'))
            the_query += lambda q: q.filter(IndexItemDetailRow.detail_name.like(bindparam('detail_name')))
            the_query += lambda q: q.filter(IndexItemDetailRow.os_is_active == True)
            the_query += lambda q: q.order_by(IndexItemDetailRow._id)
            self.baked_queries_map["get_resolved_details_for_active_iid"] = the_query
        else:
            the_query = self.baked_queries_map["get_resolved_details_for_active_iid"]

        # params with None are turned to '%'
        params = [iid, detail_name]
        for iparam in range(len(params)):
            if params[iparam] is None: params[iparam] = '%'
        retVal = the_query(self.session).params(iid=params[0], detail_name=params[1]).all()
        return retVal

    def get_resolved_details_for_iid(self, iid, detail_name=None):
        """ get the original and inherited IndexItemDetailRow's for a specific detail
            for specific iid - regardless if detail is in active os
        """
        if "get_resolved_details_for_iid" not in self.baked_queries_map:
            the_query = self.bakery(lambda session: session.query(IndexItemDetailRow))
            the_query += lambda q: q.filter(IndexItemDetailRow.owner_iid == bindparam('iid'))
            the_query += lambda q: q.filter(IndexItemDetailRow.detail_name.like(bindparam('detail_name')))
            the_query += lambda q: q.order_by(IndexItemDetailRow._id)
            self.baked_queries_map["get_resolved_details_for_iid"] = the_query
        else:
            the_query = self.baked_queries_map["get_resolved_details_for_iid"]

        # params with None are turned to '%'
        params = [iid, detail_name]
        for iparam in range(len(params)):
            if params[iparam] is None: params[iparam] = '%'
        retVal = the_query(self.session).params(iid=params[0], detail_name=params[1]).all()
        return retVal

    def get_resolved_details_value_for_active_iid(self, iid, detail_name, unique_values=False):
        """ get the original and inherited values for a specific detail
            for specific iid - but only if iid is os_is_active
        """
        retVal = list()
        distinct = "DISTINCT" if unique_values else ""
        query_text = """
            SELECT {distinct} detail_value
            FROM IndexItemDetailRow
            WHERE owner_iid = :iid
            AND detail_name = :detail_name
            AND os_is_active = 1
            ORDER BY _id
        """.format(distinct=distinct)
        try:
            exec_result = self.session.execute(query_text, {'iid': iid, 'detail_name': detail_name})
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal = [mm[0] for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_resolved_details_value_for_iid(self, iid, detail_name, unique_values=False):
        """ get the original and inherited values for a specific detail
            for specific iid - regardless if detail is in active os
        """
        distinct = "DISTINCT" if unique_values else ""
        retVal = list()
        query_text = """
            SELECT {distinct} detail_value
            FROM IndexItemDetailRow
            WHERE owner_iid = :iid
            AND detail_name = :detail_name
            ORDER BY _id
        """.format(distinct=distinct)
        try:
            exec_result = self.session.execute(query_text, {'iid': iid, 'detail_name': detail_name})
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal = [mm[0] for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_details_by_name_for_all_iids(self, detail_name):
        """ get all IndexItemDetailRow objects with detail_name.
            detail_name can contain wildcards e.g. require_%
        """
        if "get_details_by_name_for_all_iids" not in self.baked_queries_map:
            the_query = self.bakery(lambda session: session.query(IndexItemDetailRow))
            the_query += lambda q: q.filter(IndexItemDetailRow.detail_name.like(bindparam('detail_name')))
            the_query += lambda q: q.filter(IndexItemDetailRow.os_is_active == True)
            the_query += lambda q: q.order_by(IndexItemDetailRow.owner_iid)
            self.baked_queries_map["get_details_by_name_for_all_iids"] = the_query
        else:
            the_query = self.baked_queries_map["get_details_by_name_for_all_iids"]

        retVal = the_query(self.session).params(detail_name=detail_name).all()
        return retVal

    def get_detail_values_by_name_for_all_iids(self, detail_name):
        """ get values of specific detail for all iids
        """
        retVal = list()
        query_text = """
            SELECT DISTINCT detail_value
            FROM IndexItemDetailRow
            WHERE detail_name LIKE :detail_name
            AND os_is_active = 1
            ORDER BY _id
        """
        try:
            exec_result = self.session.execute(query_text, {'detail_name': detail_name})
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal = [mm[0] for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise
        return retVal

    def resolve_item_inheritance(self, item_to_resolve, generation=0):
        # print("-"*generation, " ", item_to_resolve.iid)
        iids_to_inherit_from = self.get_original_details_values_for_active_iid(item_to_resolve.iid, 'inherit')
        for original_iid in iids_to_inherit_from:
            sub_item = self.get_index_item(original_iid)
            if sub_item:
                if not sub_item.inherit_resolved:
                    self.resolve_item_inheritance(sub_item, 0)
                details_of_inherited_item = self.get_resolved_details_for_active_iid(sub_item.iid)
                for d_of_ii in details_of_inherited_item:
                    if d_of_ii.detail_name not in self.not_inherit_details:
                        inherited_detail = IndexItemDetailRow(original_iid=d_of_ii.original_iid, owner_iid=item_to_resolve.iid, os_id=d_of_ii.os_id, detail_name=d_of_ii.detail_name, detail_value=d_of_ii.detail_value, generation=d_of_ii.generation+1)
                        self.session.add(inherited_detail)
            else:
                print(item_to_resolve.iid, "inherit from non existing", original_iid)
        item_to_resolve.inherit_resolved = True

    # @utils.timing
    def resolve_inheritance(self):
        items = self.get_all_index_items()
        for item in items:
            if not item.inherit_resolved:
                self.resolve_item_inheritance(item)
        self.commit_changes()

    def item_from_index_node(self, the_iid, the_node):
        item = IndexItemRow(iid=the_iid, inherit_resolved=False, from_index=True)
        original_details = self.read_item_details_from_node(the_iid, the_node)
        return item, original_details

    def read_item_details_from_node(self, the_iid, the_node, the_os='common'):
        details = list()
        # go through the raw yaml nodes instead of doing "for detail_name in the_node".
        # this is to overcome index.yaml with maps that have two keys with the same name.
        # Although it's not valid yaml some index.yaml versions have this problem.
        for detail_node in the_node.value:
            detail_name = detail_node[0].value
            if detail_name in IndexItemsTable.os_names_to_num:
                os_specific_details = self.read_item_details_from_node(the_iid, detail_node[1], the_os=detail_name)
                details.extend(os_specific_details)
            elif detail_name == 'actions':
                actions_details = self.read_item_details_from_node(the_iid, detail_node[1], the_os)
                details.extend(actions_details)
            else:
                for details_line in detail_node[1]:
                    tag = details_line.tag if details_line.tag[0] == '!' else None
                    value = details_line.value
                    if detail_name in ("install_sources", "previous_sources") and tag is None:
                        tag = '!dir'
                    elif detail_name == "guid":
                        value = value.lower()

                    if detail_name == "install_sources":
                        if value.startswith('/'):  # absolute path
                            new_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=self.os_names_to_num[the_os], \
                                                            detail_name=detail_name, detail_value=value[1:], generation=0, tag=tag)
                            details.append(new_detail)
                        else:  # relative path
                            # because 'common' is in both groups this will create 2 IndexItemDetailRow
                            # if OS is 'common', and 1 otherwise
                            count_insertions = 0
                            for os_group in (('common', 'Mac', 'Mac32', 'Mac64'),
                                             ('common', 'Win', 'Win32', 'Win64')):
                                if the_os in os_group:
                                    item_detail_os = {'Mac32': 'Mac32', 'Mac64': 'Mac64', 'Win32': 'Win32', 'Win64': 'Win64'}.get(the_os, os_group[1])
                                    path_prefix_os = {'Mac32': 'Mac', 'Mac64': 'Mac', 'Win32': 'Win', 'Win64': 'Win'}.get(the_os, os_group[1])
                                    assert path_prefix_os == "Mac" or path_prefix_os == "Win", "path_prefix_os: {}".format(path_prefix_os)
                                    new_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=self.os_names_to_num[item_detail_os], \
                                                                    detail_name=detail_name, detail_value="/".join((path_prefix_os, value)), generation=0, tag=tag)
                                    details.append(new_detail)
                                    count_insertions += 1
                            assert count_insertions < 3, "count_insertions: {}".format(count_insertions)
                    else:
                        new_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=self.os_names_to_num[the_os], \
                                                        detail_name=detail_name, detail_value=value, generation=0, tag=tag)
                        details.append(new_detail)
        return details

    def read_index_node(self, a_node):
        index_items = list()
        items_details = list()
        for IID in a_node:
            item, original_item_details = self.item_from_index_node(IID, a_node[IID])
            index_items.append(item)
            items_details.extend(original_item_details)
        self.session.add_all(index_items)
        self.session.add_all(items_details)
        self.commit_changes()

    # @utils.timing
    def read_require_node(self, a_node):
        require_items = dict()
        if a_node.isMapping():
            all_iids = self.get_all_iids()
            for IID in a_node:
                require_details = self.read_item_details_from_require_node(IID, a_node[IID], all_iids)
                if require_details:
                    require_items[IID] = require_details
        self.insert_require_to_db(require_items)

    def read_item_details_from_require_node(self, the_iid, the_node, all_iids):
        os_id=self.os_names_to_num['common']
        details = list()
        if the_node.isMapping():
            for detail_name in the_node:
                if detail_name == "guid":
                    for guid_sub in the_node["guid"]:
                        new_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=os_id, detail_name="require_guid", detail_value=guid_sub.value)
                        details.append(new_detail)
                elif detail_name == "version":
                     for version_sub in the_node["version"]:
                        new_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=os_id, detail_name="require_version", detail_value=version_sub.value)
                        details.append(new_detail)
                elif detail_name == "require_by":
                    for require_by in the_node["require_by"]:
                        if require_by.value in all_iids:
                            detail_name = "require_by"
                        else:
                            detail_name = "deprecated_require_by"
                        new_detail = IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=os_id, detail_name=detail_name, detail_value=require_by.value)
                        details.append(new_detail)
        elif the_node.isSequence():
            for require_by in the_node:
                if require_by.value in all_iids:
                    detail_name = "require_by"
                else:
                    detail_name = "deprecated_require_by"
                details.append(IndexItemDetailRow(original_iid=the_iid, owner_iid=the_iid, os_id=os_id, detail_name=detail_name, detail_value=require_by.value))
        return details

    def repr_item_for_yaml(self, iid):
        item_details = OrderedDict()
        for os_name, os_num in self.os_names_to_num.items():
            details_rows = self.get_original_details(iid=iid, in_os=os_num)
            if len(details_rows) > 0:
                if os_name == "common":
                    work_on_dict = item_details
                else:
                    work_on_dict = item_details[os_name] = OrderedDict()
                for details_row in details_rows:
                    if details_row.detail_name in self.action_types:
                        if 'actions' not in work_on_dict:
                            work_on_dict['actions'] = OrderedDict()
                        if details_row.detail_name not in work_on_dict['actions']:
                            work_on_dict['actions'][details_row.detail_name] = list()
                        work_on_dict['actions'][details_row.detail_name].append(details_row.detail_value)
                    else:
                        if details_row.detail_name not in work_on_dict:
                            work_on_dict[details_row.detail_name] = list()
                        work_on_dict[details_row.detail_name].append(details_row.detail_value)
        return item_details

    def repr_for_yaml(self):
        retVal = OrderedDict()
        the_items = self.get_all_index_items()
        for item in the_items:
            retVal[item.iid] = self.repr_item_for_yaml(item.iid)
        return retVal

    def versions_report(self, report_only_installed=False):
        retVal = list()
        query_text = """
           SELECT *
          FROM 'report_versions_view'
        """
        if report_only_installed:
           query_text += """
           WHERE require_version != '_'
           AND remote_version != '_'
           """

        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal.extend([mm[:5] for mm in fetched_results])
        except SQLAlchemyError as ex:
            raise

        return retVal

    def iids_from_guids(self, guid_list):
        translated_iids = list()
        orphaned_guids = list()
        if guid_list:
            self.session.execute("""
            CREATE TEMP TABLE guid_to_iid_temp_t
            (
                _id  INTEGER PRIMARY KEY,
                guid VARCHAR,
                iid  VARCHAR
            );
            """)
            # add all guids to table guid_to_iid_temp_t with iid field defaults to Null
            for a_guid in list(set(guid_list)):  # list(set()) will remove duplicates
                self.session.execute("""INSERT INTO guid_to_iid_temp_t (guid) VALUES (:guid)""", {"guid": a_guid})

            # insert to table guid_to_iid_temp_t guid, iid pairs.
            # a guid might yield 0, 1, or more iids
            query_text = """
                INSERT INTO guid_to_iid_temp_t(guid, iid)
                SELECT IndexItemDetailRow.detail_value, IndexItemDetailRow.owner_iid
                FROM IndexItemDetailRow
                WHERE
                    IndexItemDetailRow.detail_name='guid'
                    AND IndexItemDetailRow.detail_value IN (SELECT guid FROM guid_to_iid_temp_t WHERE iid IS NULL);
                """
            self.session.execute(query_text)

            # return a list of guids with count of 1 which are guids that could not be translated to iids
            query_text = """
                SELECT guid FROM guid_to_iid_temp_t
                GROUP BY guid
                HAVING count(guid) < 2;
                """
            counted_orphaned_guids = self.session.execute(query_text).fetchall()
            orphaned_guids.extend([iid[0] for iid in counted_orphaned_guids])

            not_null_iids = self.session.execute("""SELECT DISTINCT iid FROM guid_to_iid_temp_t WHERE iid NOTNULL ORDER BY iid""").fetchall()
            translated_iids.extend([iid[0] for iid in not_null_iids])

            self.session.execute("""DROP TABLE guid_to_iid_temp_t;""")
        return translated_iids, orphaned_guids

    # find which iids are in the database
    def iids_from_iids(self, iid_list):
        existing_iids = None
        orphan_iids = None
        query_vars = '("'+'","'.join(iid_list)+'")'
        query_text = """
            SELECT iid
            FROM IndexItemRow
            WHERE iid IN {0}
        """.format(query_vars)

        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                existing_iids = [mm[0] for mm in fetched_results]
                # query will return list those iid in iid_list that were found in the index
                orphan_iids = list(set(iid_list)-set(existing_iids))
        except SQLAlchemyError as ex:
            raise
        return existing_iids, orphan_iids

    def get_recursive_dependencies(self, look_for_status=1):
        retVal = list()
        query_text = """
            WITH RECURSIVE find_dependants(_IID_) AS
            (
            SELECT iid FROM IndexItemRow
            WHERE install_status={} AND ignore = 0
            UNION

            SELECT IndexItemDetailRow.detail_value
            FROM IndexItemDetailRow, find_dependants
            WHERE
                IndexItemDetailRow.detail_name = 'depends'
            AND
                IndexItemDetailRow.owner_iid = find_dependants._IID_
            AND
                IndexItemDetailRow.os_is_active = 1
            )
            SELECT _IID_ FROM find_dependants
        """.format(look_for_status)

        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal = [mm[0] for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise

        return retVal

    def change_status_of_iids_to_another_status(self, old_status, new_status, iid_list):
        if iid_list:
            query_vars = '("' + '","'.join(iid_list) + '")'
            query_text = """
                UPDATE IndexItemRow
                SET install_status={new_status}
                WHERE install_status={old_status}
                AND iid IN {query_vars}
                AND ignore = 0
              """.format(**locals())
            self.session.execute(query_text)

    def change_status_of_iids(self, new_status, iid_list):
        if iid_list:
            query_vars = '("' + '","'.join(iid_list) + '")'
            query_text = """
                UPDATE IndexItemRow
                SET install_status={new_status}
                WHERE iid IN {query_vars}
                AND ignore = 0
              """.format(**locals())
            self.session.execute(query_text)

    def change_status_of_all_iids(self, new_status):
        query_text = """
            UPDATE IndexItemRow
            SET install_status=:new_status
        """
        self.session.execute(query_text, {'new_status': new_status})
        self.commit_changes()

    def get_iids_by_status(self, min_status, max_status=None):
        if max_status is None:
            max_status = min_status
        retVal = list()
        query_text = """
            SELECT iid
            FROM IndexItemRow
            WHERE install_status >= :min_status
            AND install_status <= :max_status
            AND ignore = 0
        """
        try:
            exec_result = self.session.execute(query_text, {'min_status': min_status, 'max_status': max_status})
            if exec_result.returns_rows:
                fetched_results = exec_result.fetchall()
                retVal = [mm[0] for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise
        return retVal

    def select_versions_for_installed_item(self):
        retVal = list()
        query_text = """
            SELECT IndexItemDetailRow.owner_iid, IndexItemDetailRow.detail_name, IndexItemDetailRow.detail_value, min(IndexItemDetailRow.generation)
            FROM IndexItemRow, IndexItemDetailRow
            WHERE IndexItemRow.install_status > 0
            AND IndexItemRow.ignore = 0
            AND IndexItemRow.iid=IndexItemDetailRow.owner_iid
            AND IndexItemDetailRow.detail_name='version'
            GROUP BY IndexItemDetailRow.owner_iid
            """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal

    def target_folders_to_items(self):
        """ returns a list of (IID, install_folder, tag, direct_syc_indicator) """
        retVal = list()
        query_text = """
            SELECT IndexItemDetailRow.owner_iid,
                  IndexItemDetailRow.detail_value,
                  IndexItemDetailRow.tag,
                  direct_sync_t.detail_value
            FROM IndexItemDetailRow, IndexItemRow
            LEFT JOIN IndexItemDetailRow AS direct_sync_t
              ON IndexItemRow.iid=direct_sync_t.owner_iid
                AND direct_sync_t.detail_name = 'direct_sync'
                AND direct_sync_t.os_is_active = 1
            WHERE IndexItemDetailRow.detail_name="install_folders"
                AND IndexItemRow.iid=IndexItemDetailRow.owner_iid
                AND IndexItemRow.install_status != 0
                AND IndexItemRow.ignore = 0
                AND IndexItemDetailRow.os_is_active = 1
            ORDER BY IndexItemDetailRow.detail_value
            """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError:
            raise
        return retVal

    def source_folders_to_items_without_target_folders(self):
        retVal = list()
        query_text = """
           SELECT
              install_sources_t.detail_value source,
              install_sources_t.owner_iid AS iid,
              install_sources_t.tag AS tag
            FROM IndexItemDetailRow AS install_sources_t, IndexItemRow
            WHERE install_sources_t.owner_iid NOT IN (
                SELECT DISTINCT install_folders_t.owner_iid
                FROM IndexItemDetailRow AS install_folders_t, IndexItemRow
                WHERE install_folders_t.detail_name = "install_folders"
                      AND IndexItemRow.iid = install_folders_t.owner_iid
                      AND IndexItemRow.install_status > 0
                      AND IndexItemRow.ignore = 0
                      AND install_folders_t.os_is_active = 1
                ORDER BY install_folders_t.owner_iid
            )
            AND install_sources_t.detail_name="install_sources"
                AND IndexItemRow.iid = install_sources_t.owner_iid
                AND IndexItemRow.install_status != 0
                AND IndexItemRow.ignore = 0
                AND install_sources_t.os_is_active = 1
            """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal

    def name_and_version_report_for_active_iids(self):
        query_text = """
            SELECT IndexItemRow.iid,
                    coalesce(name_row.detail_value, "")       AS name,
                    coalesce(version_row.detail_value, "")    AS version,
                    min(version_row.generation) AS ver_gen,
                    min(name_row.generation)    AS name_gen
            FROM IndexItemRow
                LEFT JOIN IndexItemDetailRow AS version_row
                    ON version_row.owner_iid=IndexItemRow.iid
                    AND version_row.detail_name='version'
                    AND version_row.os_is_active=1
                LEFT JOIN IndexItemDetailRow AS name_row
                    ON name_row.owner_iid=IndexItemRow.iid
                    AND name_row.detail_name='name'
                    AND name_row.os_is_active=1
            WHERE install_status!=0
            AND ignore=0
            GROUP BY IndexItemRow.iid
            """
        fetched_results = self.session.execute(query_text).fetchall()
        return fetched_results

    def get_iids_and_details_for_active_iids(self, detail_name, unique_values=False, limit_to_iids=None):
        retVal = list()
        group_by_values_filter = "GROUP BY IndexItemDetailRow.detail_value" if unique_values else ""
        limit_to_iids_filter = ""
        if limit_to_iids:
            quoted_limit_to_iids = [utils.quoteme_single(iid) for iid in limit_to_iids]
            limit_to_iids_filter = " ".join(('AND IndexItemDetailRow.owner_iid IN (', ",".join(quoted_limit_to_iids), ')'))

        query_text = """
            SELECT  IndexItemDetailRow.owner_iid, IndexItemDetailRow.detail_value
            FROM IndexItemDetailRow
                JOIN IndexItemRow
                    ON  IndexItemRow.iid=IndexItemDetailRow.owner_iid
                    AND IndexItemRow.install_status!=0
                    AND IndexItemRow.ignore = 0
            WHERE IndexItemDetailRow.detail_name=:detail_name
                AND IndexItemDetailRow.os_is_active = 1
            {limit_to_iids_filter}
            {group_by_values_filter}
            ORDER BY IndexItemDetailRow._id
            """.format(**locals())
        try:
            exec_result = self.session.execute(query_text, {'detail_name': detail_name})
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_details_for_active_iids(self, detail_name, unique_values=False, limit_to_iids=None):
        distinct = "DISTINCT" if unique_values else ""
        limit_to_iids_filter = ""
        if limit_to_iids:
            quoted_limit_to_iids = [utils.quoteme_single(iid) for iid in limit_to_iids]
            limit_to_iids_filter = " ".join(('AND IndexItemDetailRow.owner_iid IN (', ",".join(quoted_limit_to_iids), ')'))

        query_text = """
            SELECT {distinct} IndexItemDetailRow.detail_value
            FROM IndexItemDetailRow
                JOIN IndexItemRow
                    ON  IndexItemRow.iid=IndexItemDetailRow.owner_iid
                    AND IndexItemRow.install_status!=0
                    AND IndexItemRow.ignore = 0
            WHERE IndexItemDetailRow.detail_name=:detail_name
                AND IndexItemDetailRow.os_is_active = 1
                {limit_to_iids_filter}
            ORDER BY IndexItemDetailRow._id
            """.format(**locals())
        fetched_results = self.session.execute(query_text, {'detail_name': detail_name}).fetchall()
        retVal = [mm[0] for mm in fetched_results]
        return retVal

    def get_details_and_tag_for_active_iids(self, detail_name, unique_values=False, limit_to_iids=None):
        retVal = list()
        distinct = "DISTINCT" if unique_values else ""
        limit_to_iids_filter = ""
        if limit_to_iids:
            limit_to_iids_filter = 'AND IndexItemDetailRow.owner_iid IN ("'
            limit_to_iids_filter += '","'.join(limit_to_iids)
            limit_to_iids_filter += '")'

        query_text = """
            SELECT {distinct} IndexItemDetailRow.detail_value, IndexItemDetailRow.tag
            FROM IndexItemDetailRow
                JOIN IndexItemRow
                    ON  IndexItemRow.iid=IndexItemDetailRow.owner_iid
                    AND IndexItemRow.install_status!=0
                    AND IndexItemRow.ignore = 0
            WHERE IndexItemDetailRow.detail_name=:detail_name
                AND IndexItemDetailRow.os_is_active = 1
                {limit_to_iids_filter}
            ORDER BY IndexItemDetailRow._id
            """.format(**locals())
        try:
            exec_result = self.session.execute(query_text, {'detail_name': detail_name})
            if exec_result.returns_rows:
                fetched_results= exec_result.fetchall()
                retVal = [(mm[0], mm[1]) for mm in fetched_results]
        except SQLAlchemyError as ex:
            raise
        # returns: [(iid, index_version, require_version, index_guid, require_guid, generation), ...]
        return retVal

    def create_default_items(self, iids_to_ignore):
        self.create_default_index_items(iids_to_ignore=iids_to_ignore)
        self.create_default_require_items(iids_to_ignore=iids_to_ignore)

    def require_items_without_version_or_guid(self):
        retVal = list()
        query_text = """
          SELECT IndexItemRow.iid,
                index_ver_t.detail_value AS index_version,
                require_ver_t.detail_value AS require_version,
                index_guid_t.detail_value AS index_guid,
                require_guid_t.detail_value AS require_guid,
                min(index_ver_t.generation) AS generation
            from IndexItemRow
                JOIN IndexItemDetailRow AS index_ver_t ON IndexItemRow.iid = index_ver_t.owner_iid
                AND index_ver_t.detail_name='version'
                Left JOIN IndexItemDetailRow AS require_ver_t ON IndexItemRow.iid = require_ver_t.owner_iid
                AND require_ver_t.detail_name='require_version'
                JOIN IndexItemDetailRow AS index_guid_t ON IndexItemRow.iid = index_guid_t.owner_iid
                AND index_guid_t.detail_name='guid'
                Left JOIN IndexItemDetailRow AS require_guid_t ON IndexItemRow.iid = require_guid_t.owner_iid
                AND require_guid_t.detail_name='require_guid'
            WHERE from_require=1 AND (require_ver_t.detail_value ISNULL OR require_guid_t.detail_value ISNULL)
            GROUP BY IndexItemRow.iid
          """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        # returns: [(iid, index_version, require_version, index_guid, require_guid, generation), ...]
        return retVal

    def insert_binary_versions(self, binaries_version_list):
        for binary_details in binaries_version_list:
            folder, name = os.path.split(binary_details[0])
            self.session.add(FoundOnDiskItemRow(name=name, path=binary_details[0], version=binary_details[1], guid=binary_details[2]))
        self.commit_changes()

    def add_require_version_from_binaries(self):
        """ add require_version for iid that do not have this detail value (because previous index.yaml did not have it)
        1st try version found on disk from table FoundOnDiskItemRow
        2nd for iids still missing require_version try phantom_version detail value
        """
        query_text = """
        INSERT OR REPLACE INTO IndexItemDetailRow (original_iid, owner_iid, os_id, detail_name, detail_value, generation)
        SELECT  FoundOnDiskItemRow.iid, -- original_iid
                FoundOnDiskItemRow.iid, -- owner_iid
                0,                      -- os_id
                'require_version',      -- detail_name
                FoundOnDiskItemRow.version, -- detail_value from disk
                0                       -- generation
        FROM require_items_without_require_version_view
        JOIN FoundOnDiskItemRow
            ON FoundOnDiskItemRow.iid=require_items_without_require_version_view.iid
            AND FoundOnDiskItemRow.version NOTNULL
        """
        try:
            exec_result = self.session.execute(query_text)
            self.commit_changes()
        except SQLAlchemyError as ex:
            print(ex)
            raise

        query_text = """
        INSERT OR REPLACE INTO IndexItemDetailRow (original_iid, owner_iid, os_id, detail_name, detail_value, generation)
        SELECT  IndexItemDetailRow.owner_iid, -- original_iid
                IndexItemDetailRow.owner_iid, -- owner_iid
                0,                      -- os_id
                'require_version',      -- detail_name
                IndexItemDetailRow.detail_value, -- detail_value from phantom_version
                0                       -- generation
        FROM require_items_without_require_version_view
        JOIN IndexItemDetailRow
            ON IndexItemDetailRow.owner_iid=require_items_without_require_version_view.iid
            AND IndexItemDetailRow.detail_name='phantom_version'
        """
        try:
            exec_result = self.session.execute(query_text)
            self.commit_changes()
        except SQLAlchemyError as ex:
            print(ex)
            raise

    def add_require_guid_from_binaries(self):
        query_text = """
        INSERT OR REPLACE INTO IndexItemDetailRow (original_iid, owner_iid, os_id, detail_name, detail_value, generation)
        SELECT  FoundOnDiskItemRow.iid,  -- original_iid
                FoundOnDiskItemRow.iid,  -- owner_iid
                0,                       -- os_id
                'require_guid',          -- detail_name
                FoundOnDiskItemRow.guid, -- detail_value
                0                        -- generation
        FROM require_items_without_require_guid_view
        JOIN FoundOnDiskItemRow
            ON FoundOnDiskItemRow.iid=require_items_without_require_guid_view.iid
            AND FoundOnDiskItemRow.guid NOTNULL
        """
        try:
            exec_result = self.session.execute(query_text)
            self.commit_changes()
        except SQLAlchemyError as ex:
            print(ex)
            raise

    def set_ignore_iids(self, iid_list):
        if iid_list:
            query_vars = "".join((
                '(',
                ",".join(utils.quoteme_double_list(iid_list)),
                ')'))
            query_text = """
                UPDATE IndexItemRow
                SET ignore=1
                WHERE iid IN {query_vars}
              """.format(**locals())
            self.session.execute(query_text)
            self.commit_changes()

    def config_var_list_to_db(self, in_config_var_list):
        for identifier in in_config_var_list:
            raw_value = in_config_var_list.unresolved_var(identifier)
            resolved_value = in_config_var_list.ResolveVarToStr(identifier, list_sep=" ", default="")
            self.session.add(ConfigVar(name=identifier, raw_value=raw_value, resolved_value=resolved_value))
        self.commit_changes()

    def get_sync_folders_and_sources_for_active_iids(self):
        retVal = list()
        query_text = """
             SELECT install_sources_t.owner_iid AS iid,
                    direct_sync_t.detail_value AS direct_sync_indicator,
                    install_sources_t.detail_value AS source,
                    install_sources_t.tag AS tag,
                    install_folders_t.detail_value AS install_folder
            FROM IndexItemDetailRow AS install_sources_t
                JOIN IndexItemRow AS iid_t
                    ON iid_t.iid=install_sources_t.owner_iid
                    AND iid_t.install_status > 0
                LEFT JOIN IndexItemDetailRow AS install_folders_t
                    ON install_folders_t.os_is_active=1
                    AND install_sources_t.owner_iid = install_folders_t.owner_iid
                        AND install_folders_t.detail_name='install_folders'
                LEFT JOIN IndexItemDetailRow AS direct_sync_t
                    ON direct_sync_t.os_is_active=1
                    AND install_sources_t.owner_iid = direct_sync_t.owner_iid
                        AND direct_sync_t.detail_name='direct_sync'
            WHERE
                install_sources_t.os_is_active=1
                AND install_sources_t.detail_name='install_sources'
        """
        try:
            exec_result = self.session.execute(query_text)
            if exec_result.returns_rows:
                # returns [(iid, direct_sync_indicator, source, source_tag, install_folder),...]
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_sources_for_iid(self, the_iid):
        retVal = list()
        query_text = """
         SELECT
            install_sources_t.detail_value AS install_sources,
            install_sources_t.tag as tag
        FROM IndexItemRow AS iid_t, IndexItemDetailRow as install_sources_t
        WHERE
            iid_t.iid=install_sources_t.owner_iid
                AND
            install_sources_t.detail_name='install_sources'
                AND
            install_sources_t.os_is_active=1
                AND
            iid_t.iid=:the_iid
                AND
            iid_t.install_status != 0
                AND
            iid_t.ignore=0
        ORDER BY install_sources_t.detail_value
        """
        try:
            exec_result = self.session.execute(query_text, {'the_iid': the_iid})
            if exec_result.returns_rows:
                # returns [(source, source_tag),...]
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_unique_detail_values(self, detail_name):
        retVal = list()
        query_text = """
          SELECT DISTINCT IndexItemDetailRow.detail_value
          FROM IndexItemDetailRow
          WHERE detail_name = :detail_name
          ORDER BY IndexItemDetailRow.detail_value
        """
        try:
            exec_result = self.session.execute(query_text, {'detail_name': detail_name})
            if exec_result.returns_rows:
                retVal.extend([mm[0] for mm in exec_result.fetchall()])
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_iids_with_specific_detail_values(self, detail_name, detail_value):
        """ get all iids that have detail_name with specific detail_value
            detail_name, detail_value can contain wild cards, e.g.:
            get_iids_with_specific_detail_values("require_%", "%banana%")
        """
        retVal = list()
        query_text = """
            SELECT DISTINCT original_iid
            FROM IndexItemDetailRow
            WHERE
                detail_name LIKE :detail_name
            AND
                detail_value LIKE :detail_value
            """
        try:
            exec_result = self.session.execute(query_text, {'detail_name': detail_name, 'detail_value': detail_value})
            if exec_result.returns_rows:
                retVal.extend([mm[0] for mm in exec_result.fetchall()])
        except SQLAlchemyError as ex:
            raise
        return retVal

    def get_missing_iids_from_details(self, detail_name):
        """ some details' values should be existing iids
            this function will return the original iid and the orphan iids in named details
        """
        retVal = list()
        query_text = """
            SELECT DISTINCT original_iid, detail_value
            FROM IndexItemDetailRow
            WHERE
                detail_name = :detail_name
                    AND
                detail_value NOT IN (SELECT iid FROM IndexItemRow)
            ORDER BY detail_value
            """
        try:
            exec_result = self.session.execute(query_text, {'detail_name': detail_name})
            if exec_result.returns_rows:
                retVal.extend(exec_result.fetchall())
        except SQLAlchemyError as ex:
            raise
        return retVal










