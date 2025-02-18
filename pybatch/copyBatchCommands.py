import os
import shutil
from collections import defaultdict
from pathlib import Path
from typing import List
# ToDo: add unwtar ?


from .fileSystemBatchCommands import *
log = logging.getLogger(__name__)


def _fast_copy_file(src, dst):
    # insert faster code here if and when available
    length = 256*1024
    with open(src, 'rb') as rfd:
        with open(dst, 'wb') as wfd:
            while 1:
                buf = rfd.read(length)
                if not buf:
                    break
                wfd.write(buf)


class RsyncClone(PythonBatchCommandBase, essential=True):
    """ base class for copying file system objects
        tries to mimic rsync behaviour
    """

    __global_ignore_patterns = list()        # files and folders matching these patterns will not be copied. Applicable for all instances of RsyncClone
    __global_no_hard_link_patterns = list()  # files and folders matching these patterns will not be hard-linked. Applicable for all instances of RsyncClone
    __global_avoid_copy_markers = list()     # if a file with one of these names exists in the folders and is identical to destination, copy will be avoided
    __global_no_flags_patterns = list()     # if a file with one of these names exists in the destination, it's flags (hidden, system, read-only) will be removed

    @classmethod
    def add_global_ignore_patterns(cls, more_copy_ignore_patterns: List):
        cls.__global_ignore_patterns.extend(more_copy_ignore_patterns)

    @classmethod
    def add_global_no_hard_link_patterns(cls, more_no_hard_link_patterns: List):
        cls.__global_no_hard_link_patterns.extend(more_no_hard_link_patterns)

    @classmethod
    def add_global_avoid_copy_markers(cls, more_avoid_copy_markers: List):
        cls.__global_avoid_copy_markers.extend(more_avoid_copy_markers)

    @classmethod
    def add_global_no_flags_patterns(cls, more_no_flags_patterns: List):
        cls.__global_no_flags_patterns.extend(more_no_flags_patterns)

    def __init__(self,
                 src,
                 dst,
                 ignore_if_not_exist=False,
                 symlinks_as_symlinks=True,
                 ignore_patterns=[],        # files and folders matching this patterns will not be copied. Applicable only for this instance of RsyncClone
                 no_hard_link_patterns=[],  # files and folders matching this patterns will not be hard-linked. Applicable only for this instance of RsyncClone
                 no_flags_patterns=[],
                 hard_links=True,
                 ignore_dangling_symlinks=False,
                 delete_extraneous_files=False,
                 copy_owner=False,
                 verbose=0,
                 dry_run=False,
                 copy_stat=False,
                 **kwargs):
        super().__init__(**kwargs)
        self.src = src
        self.dst = dst
        self.ignore_if_not_exist = ignore_if_not_exist
        self.symlinks_as_symlinks = symlinks_as_symlinks
        self.local_ignore_patterns = sorted(ignore_patterns.copy())
        self.local_no_hard_link_patterns = sorted(no_hard_link_patterns.copy())
        self.local_no_flags_patterns = sorted(no_flags_patterns.copy())
        self.hard_links = hard_links
        self.hard_links_failed = False  # remember if hard linking failed once so save time not to try again
        self.ignore_dangling_symlinks = ignore_dangling_symlinks
        self.delete_extraneous_files = delete_extraneous_files
        self.copy_owner = copy_owner
        self.has_chown = hasattr(os, 'chown')
        self.verbose = verbose
        self.dry_run = dry_run
        self.copy_stat = copy_stat
        self.top_destination_does_not_exist = False  # will be set to true is destination does not exist saving many checks

        self._get_ignored_files_func = None
        self.statistics = defaultdict(int)
        self.last_step = None
        self.last_src = None
        self.last_dst = None

        if self.ignore_all_errors:
            self.ignore_if_not_exist = True  # self.ignore_if_not_exist is passed to shutil calls that do not know about self.ignore_all_errors

        if self.ignore_if_not_exist:
            self.exceptions_to_ignore.append(FileNotFoundError)

        self.__all_ignore_patterns = sorted(list(set(self.__global_ignore_patterns + self.local_ignore_patterns)))
        self.__all_no_hard_link_patterns = sorted(list(set(self.__global_no_hard_link_patterns + self.local_no_hard_link_patterns)))
        self.__all_no_flags_patterns = sorted(list(set(self.__global_no_flags_patterns + self.local_no_flags_patterns)))

    def repr_own_args(self, all_args: List[str]) -> None:
        params = list()
        params.append(self.unnamed__init__param(os.fspath(self.src)))
        params.append(self.unnamed__init__param(os.fspath(self.dst)))
        if not self.ignore_all_errors:
            params.append(self.optional_named__init__param("ignore_if_not_exist", self.ignore_if_not_exist, False))
        params.append(self.optional_named__init__param("symlinks_as_symlinks", self.symlinks_as_symlinks, True))
        params.append(self.optional_named__init__param("ignore_patterns", self.local_ignore_patterns, []))
        params.append(self.optional_named__init__param("no_hard_link_patterns", self.local_no_hard_link_patterns, []))
        params.append(self.optional_named__init__param("no_flags_patterns", self.local_no_flags_patterns, []))
        params.append(self.optional_named__init__param("hard_links", self.hard_links, True))
        params.append(self.optional_named__init__param("ignore_dangling_symlinks", self.ignore_dangling_symlinks, False))
        params.append(self.optional_named__init__param("delete_extraneous_files", self.delete_extraneous_files, False))
        params.append(self.optional_named__init__param("copy_owner", self.copy_owner, False))
        params.append(self.optional_named__init__param("verbose", self.verbose, 0))
        params.append(self.optional_named__init__param("dry_run", self.dry_run, False))
        params.append(self.optional_named__init__param("copy_stat", self.copy_stat, False))
        all_args.extend(filter(None, params))

    def progress_msg_self(self) -> str:
        return f"""Copy '{os.path.expandvars(self.src)}' to '{os.path.expandvars(self.dst)}'"""

    def __call__(self, *args, **kwargs) -> None:
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        resolved_src: Path = utils.ExpandAndResolvePath(self.src)
        resolved_dst: Path = utils.ExpandAndResolvePath(self.dst)
        self.top_destination_does_not_exist = not resolved_dst.exists()
        self.copy_tree(resolved_src, resolved_dst)

    def should_ignore_file(self, file_path: str):
        retVal = False
        if not isinstance(file_path, Path):
            file_path = Path(file_path)
        for ignore_pattern in self.__all_ignore_patterns:
            if file_path.match(ignore_pattern):
                log.debug(f"ignoring {file_path} because it matches pattern {ignore_pattern}")
                retVal = True
                break
        return retVal

    def should_hard_link_file(self, file_path: Path):
        assert isinstance(file_path, Path)
        retVal = False
        if self.hard_links and not self.hard_links_failed and not file_path.is_symlink():
            for no_hard_link_pattern in self.__all_no_hard_link_patterns:
                if file_path.match(no_hard_link_pattern):
                    log.debug(f"not hard linking {file_path} because it matches pattern {no_hard_link_pattern}")
                    break
            else:
                retVal = True
        return retVal

    def should_hard_link_file_DirEntry(self, a_file: os.DirEntry):
        assert isinstance(a_file, os.DirEntry)
        retVal = False
        if self.hard_links and not self.hard_links_failed and not a_file.is_symlink():
            for no_hard_link_pattern in self.__all_no_hard_link_patterns:
                file_path = Path(a_file)  # todo: avoid using Path.match, since converting DirEntry toPath is not efficient
                if file_path.match(no_hard_link_pattern):
                    log.debug(f"not hard linking {a_file.path} because it matches pattern {no_hard_link_pattern}")
                    break
            else:
                retVal = True
        return retVal

    def should_no_flags_file(self, file_path: Path):
        retVal = True
        for no_flags_pattern in self.__all_no_flags_patterns:
            if file_path.match(no_flags_pattern):
                log.debug(f"removing flags from {file_path} because it matches pattern {no_flags_pattern}")
                break
        else:
            retVal = False
        return retVal

    def remove_extraneous_files(self, dst: Path, src_item_names):
        """ remove files in destination that are not in source.
            files in the ignore list are not removed even if they do not
            appear in the source.
        """

        for dst_item in os.scandir(dst):
            if dst_item.name not in src_item_names and not self.should_ignore_file(dst_item.path):
                self.last_step, self.last_src, self.last_dst = "remove redundant file", "", os.fspath(dst_item)
                log.info(f"delete {dst_item.path}")
                if dst_item.is_symlink() or dst_item.is_file():
                    self.dry_run or os.unlink(dst_item)
                else:
                    self.dry_run or shutil.rmtree(dst_item)

    def copy_symlink(self, src_path: Path, dst_path: Path):
        self.last_src, self.last_dst = os.fspath(src_path), os.fspath(dst_path)
        self.doing = f"""copy symlink '{self.last_src}' to '{self.last_dst}'"""

        link_to = os.readlink(src_path)
        if self.symlinks_as_symlinks:
            self.dry_run or os.symlink(link_to, dst_path)
            self.dry_run or shutil.copystat(src_path, dst_path, follow_symlinks=False)
            log.debug(f"create symlink '{dst_path}'")
        else:
            # ignore dangling symlink if the flag is on
            if not os.path.exists(link_to) and self.ignore_dangling_symlinks:  # !
                return
            # otherwise let the copy occur. copy_file_to_file will raise an error
            log.debug(f"copy symlink contents '{src_path}' to '{dst_path}'")
            if src_path.is_dir():
                self.copy_tree(src_path, dst_path)
            else:
                self.copy_file_to_file(src_path, dst_path)

    def should_copy_file_Path(self, src: Path, dst: Path):
        retVal = True
        if not self.top_destination_does_not_exist:
            try:
                dst_stats = dst.stat()
                src_stats = src.stat()
                if src_stats.st_ino == dst_stats.st_ino:
                    retVal = False
                    log.debug(f"{self.progress_msg()} skip copy file, same inode '{src}' to '{dst}'")
                elif src_stats.st_size == dst_stats.st_size and src_stats.st_mtime == dst_stats.st_mtime:
                    retVal = False
                    log.debug(f"{self.progress_msg()} skip copy file, same time and size '{src}' to '{dst}'")
                if retVal:  # destination exists and file should be copied, so make sure it's writable
                    with Chmod(dst, "a+rw", own_progress_count=0) as mod_changer:
                        mod_changer()
                    if self.should_no_flags_file(dst):
                        with ChFlags(dst, "nohidden", "nosystem", "unlocked", ignore_all_errors=True, own_progress_count=0) as flags_changer:
                            flags_changer()
            except Exception as ex:  # most likely dst.stat() failed because dst does not exist
                retVal = True
        return retVal

    def should_copy_file_DirEntry(self, src: os.DirEntry, dst: Path):
        retVal = True
        if not self.top_destination_does_not_exist:
            try:
                dst_stats = dst.stat()
                src_stats = src.stat()
                if src_stats.st_ino == 0:  # on windows os.DirEntry.stat sets st_ino to zero and os.stat should be called
                                           # see https://docs.python.org/3.6/library/os.html#os.DirEntry
                    src_stats = os.stat(src.path, follow_symlinks=False)
                if src_stats.st_ino == dst_stats.st_ino:
                    retVal = False
                    log.debug(f"{self.progress_msg()} skip copy file, same inode '{src.path}' to '{dst}'")
                elif src_stats.st_size == dst_stats.st_size and src_stats.st_mtime == dst_stats.st_mtime:
                    retVal = False
                    log.debug(f"{self.progress_msg()} skip copy file, same time and size '{src.path}' to '{dst}'")
                if retVal:  # destination exists and file should be copied, so make sure it's writable
                    with Chmod(dst, "a+rw", own_progress_count=0) as mod_changer:
                        mod_changer()
                    if self.should_no_flags_file(dst):
                        with ChFlags(dst, "nohidden", "nosystem", "unlocked", ignore_all_errors=True, own_progress_count=0) as flags_changer:
                            flags_changer()
            except Exception as ex:  # most likely dst.stat() failed because dst does not exist
                retVal = True
        return retVal

    def should_copy_dir(self, src: Path, dst: Path, src_file_names):
        retVal = self.top_destination_does_not_exist
        if not retVal:
            for avoid_copy_marker in self.__global_avoid_copy_markers:
                if avoid_copy_marker in src_file_names:
                    src_marker = Path(src, avoid_copy_marker)
                    dst_marker = Path(dst, avoid_copy_marker)
                    retVal = not utils.compare_files_by_checksum(dst_marker, src_marker)
                    if not retVal:
                        log.debug(f"{self.progress_msg()} skip copy folder, same checksum '{src_marker}' and '{dst_marker}'")
                        break
            else:
                retVal = True
        return retVal

    def copy_file_to_file(self, src: Path, dst: Path, follow_symlinks=True):
        """ copy the file src to the file dst. dst should either be an existing file
            or not exists at all - i.e. dst cannot be a folder. The parent folder of dst
            is assumed to exist
        """
        self.last_src, self.last_dst = os.fspath(src), os.fspath(dst)
        self.doing = f"""copy file '{self.last_src}' to '{self.last_dst}'"""

        if self.should_copy_file_Path(src, dst):
            if not self.should_hard_link_file(src):
                log.debug(f"copy file '{self.last_src}' to '{self.last_dst}'")
                if not self.dry_run:
                    _fast_copy_file(src, dst)
                    if self.copy_stat:
                        shutil.copystat(src, dst, follow_symlinks=follow_symlinks)
            else:  # try to create hard link
                try:
                    self.dry_run or os.link(src, dst)
                    log.debug(f"hard link file '{self.last_src}' to '{self.last_dst}'")
                    self.statistics['hard_links'] += 1
                except OSError as ose:
                    self.hard_links_failed = True
                    log.debug(f"copy file '{self.last_src}' to '{self.last_dst}'")

                    if not self.dry_run:
                        _fast_copy_file(src, dst)
                        if self.copy_stat:
                            shutil.copystat(src, dst, follow_symlinks=follow_symlinks)
            if self.copy_owner and self.has_chown:
                src_st = src.stat()
                os.chown(dst, src_st[stat.ST_UID], src_st[stat.ST_GID])
        else:
            self.statistics['skipped_files'] += 1
        return dst

    def copy_file_to_file_DirEntry(self, src: os.DirEntry, dst: Path, follow_symlinks=True):
        """ copy the file src to the file dst. dst should either be an existing file
            or not exists at all - i.e. dst cannot be a folder. The parent folder of dst
            is assumed to exist.
            src is assumed to be of type os.DirEntry
        """
        self.last_src, self.last_dst = os.fspath(src), os.fspath(dst)
        self.doing = f"""copy file '{self.last_src}' to '{self.last_dst}'"""

        if self.should_copy_file_DirEntry(src, dst):
            if not self.should_hard_link_file_DirEntry(src):
                log.debug(f"copy file '{self.last_src}' to '{self.last_dst}'")
                if not self.dry_run:
                    _fast_copy_file(src, dst)
                    shutil.copystat(src, dst, follow_symlinks=follow_symlinks)
            else:  # try to create hard link
                try:
                    self.dry_run or os.link(src, dst)
                    log.debug(f"hard link file '{self.last_src}' to '{self.last_dst}'")
                    self.statistics['hard_links'] += 1
                except OSError as ose:
                    self.hard_links_failed = True
                    log.debug(f"copy file '{self.last_src}' to '{self.last_dst}'")

                    if not self.dry_run:
                        _fast_copy_file(src, dst)
                        shutil.copystat(src, dst, follow_symlinks=follow_symlinks)
            if self.copy_owner and self.has_chown:
                src_st = src.stat()  # !
                os.chown(dst, src_st[stat.ST_UID], src_st[stat.ST_GID])
        else:
            self.statistics['skipped_files'] += 1
        return dst

    def copy_file_to_dir(self, src: Path, dst: Path, follow_symlinks=True):
        self.last_src, self.last_dst = os.fspath(src), os.fspath(dst)
        self.doing = f"""copy file '{self.last_src}' to '{self.last_dst}'"""

        if self.top_destination_does_not_exist:
            dst.mkdir(parents=True, exist_ok=True)
        final_dst = dst.joinpath(src.name)
        retVal = self.copy_file_to_file(src, final_dst, follow_symlinks)
        return retVal

    def copy_tree(self, src: Path, dst: Path):
        """ based on shutil.copytree
        """
        self.last_src, self.last_dst = os.fspath(src), os.fspath(dst)
        save_top_destination_does_not_exist = self.top_destination_does_not_exist
        self.top_destination_does_not_exist = self.top_destination_does_not_exist or not dst.exists()  # !

        self.doing = f"""copy folder '{self.last_src}' to '{self.last_dst}'"""

        src_dir_items = list(os.scandir(src))
        src_file_names = [src_item.name for src_item in src_dir_items if src_item.is_file()]
        src_dir_names = [src_item.name for src_item in src_dir_items if src_item.is_dir()]
        if not self.should_copy_dir(src, dst, src_file_names):
            self.statistics['skipped_dirs'] += 1
            return

        self.statistics['dirs'] += 1
        log.debug(f"copy folder '{src}' to '{dst}'")

        if self.top_destination_does_not_exist:
            dst.mkdir(parents=True, exist_ok=True)
            if self.copy_owner and self.has_chown:
                src_stat = src.stat()
                os.chown(dst, src_stat[stat.ST_UID], src_stat[stat.ST_GID])

        if not self.top_destination_does_not_exist and self.delete_extraneous_files:
            self.remove_extraneous_files(dst, src_file_names+src_dir_names)

        errors = []
        for src_item in src_dir_items:
            src_item_path = Path(src_item.path)
            if self.should_ignore_file(src_item_path):
                self.statistics['ignored'] += 1
                continue
            dst_path = dst.joinpath(src_item.name)
            try:
                if src_item.is_symlink():
                    self.statistics['symlinks'] += 1
                    self.copy_symlink(src_item_path, dst_path)
                elif src_item.is_dir():
                    self.copy_tree(src_item_path, dst_path)
                else:
                    self.statistics['files'] += 1
                    # Will raise a SpecialFileError for unsupported file types
                    self.copy_file_to_file_DirEntry(src_item, dst_path)
            # catch the Error from the recursive copytree so that we can
            # continue with other files
            except shutil.Error as err:
                errors.append(err.args[0])
            except OSError as why:
                errors.append((src_item_path, dst_path, str(why)))
        try:
            shutil.copystat(src, dst)
        except OSError as why:
            # Copying file access times may fail on Windows
            if getattr(why, 'winerror', None) is None:
                errors.append((src, dst, str(why)))
        if errors:
            raise shutil.Error(errors)
        self.top_destination_does_not_exist = save_top_destination_does_not_exist
        return dst

    def error_dict_self(self, exc_type, exc_val, exc_tb) -> None:
        super().error_dict_self(exc_type, exc_val, exc_tb)

        last_src_path = "unknown"
        last_src_mode = "unknown"
        try:
            last_src_path = Path(self.last_src)
            last_src_mode = utils.unix_permissions_to_str(last_src_path.lstat().st_mode)
        except:
            pass

        last_dst_path = "unknown"
        last_dst_mode = "unknown"
        try:
            last_dst_path = Path(self.last_dst)
            last_dst_mode = utils.unix_permissions_to_str(last_dst_path.lstat().st_mode)
        except:
            pass

        self._error_dict.update(
            {'last_src':  {"path": os.fspath(last_src_path), "mode": last_src_mode},
             'last_dst':  {"path": os.fspath(last_dst_path), "mode": last_dst_mode}})


class CopyDirToDir(RsyncClone):
    """ copy a folder into another
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs) -> None:
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        resolved_src: Path = utils.ExpandAndResolvePath(self.src)
        resolved_dst: Path = utils.ExpandAndResolvePath(self.dst)
        final_dst: Path = resolved_dst.joinpath(resolved_src.name)
        self.top_destination_does_not_exist = not final_dst.exists()
        self.copy_tree(resolved_src, final_dst)


class MoveDirToDir(CopyDirToDir):
    """ copy a folder into another and erase the source
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs):
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        try:
            super().__call__(*args, **kwargs)
        except Exception as ex:
            raise
        else:  # do not attempt remove if copy did not work
            self.doing = f"""removing dir '{self.src}'"""
            self.dry_run or shutil.rmtree(self.src, ignore_errors=self.ignore_if_not_exist)


class CopyDirContentsToDir(RsyncClone):
    """ copy the contents of a folder into another
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)


class MoveDirContentsToDir(CopyDirContentsToDir):
    """ copy the contents of a folder into another and erase the sources
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs):
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        try:
            super().__call__(*args, **kwargs)
        except Exception as ex:
            raise
        else:  # do not attempt remove if copy did not work
            self.doing = f"""removing contents dir '{self.src}'"""
            if not self.dry_run:
                for child_item in Path(self.src).iterdir():
                    if child_item.is_file():
                        child_item.unlink()
                    elif child_item.is_dir():
                        shutil.rmtree(child_item, ignore_errors=self.ignore_if_not_exist)


class CopyFileToDir(RsyncClone):
    """ copy a file into a folder
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs):
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        resolved_src: Path = utils.ExpandAndResolvePath(self.src)
        resolved_dst: Path = utils.ExpandAndResolvePath(self.dst)
        self.top_destination_does_not_exist = not resolved_dst.exists()
        self.copy_file_to_dir(resolved_src, resolved_dst)


class MoveFileToDir(CopyFileToDir):
    """ copy a file into a folder and erase the source
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs):
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        try:
            super().__call__(*args, **kwargs)
        except Exception as ex:
            raise
        else:  # do not attempt remove if copy did not work
            self.doing = f"""removing file '{self.src}'"""
            self.dry_run or Path(self.src).unlink()


class CopyFileToFile(RsyncClone):
    """ copy a file into another location
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs) -> None:
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        resolved_src: Path = utils.ExpandAndResolvePath(self.src)
        resolved_dst: Path = utils.ExpandAndResolvePath(self.dst)
        resolved_dst.parent.mkdir(parents=True, exist_ok=True)
        self.top_destination_does_not_exist = False
        self.copy_file_to_file(resolved_src, resolved_dst)


class MoveFileToFile(CopyFileToFile):
    """ copy a file into another location and erase the source
        intermediate folders will be created as needed
    """
    def __init__(self, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)

    def __call__(self, *args, **kwargs) -> None:
        PythonBatchCommandBase.__call__(self, *args, **kwargs)
        try:
            super().__call__(*args, **kwargs)
        except Exception as ex:
            raise
        else:  # do not attempt remove if copy did not work
            self.doing = f"""removing file '{self.src}'"""
            self.dry_run or Path(self.src).unlink()


class RenameFile(MoveFileToFile):
    """ copy a file into another location and erase the source
        intermediate folders will be created as needed
    """
    pass


class CopyBundle(RsyncClone):
    """ Do all that is needed in order to copy a bundle:
        - unwtar files
        - copy not wtar files
        - set permissions and ownership
        Optionally avoid copying by comparing Info.xml or Info.plist
    """

    def __init__(self, source, destination, unwtar=False, **kwargs):
        super().__init__(src=None, dst=None, **kwargs)
        self.source = Path(source)
        self.destination = Path(destination)
        self.unwtar = unwtar

    def repr_own_args(self, all_args: List[str]) -> None:
        all_args.append(self.unnamed__init__param(os.fspath(self.source)))
        all_args.append(self.unnamed__init__param(os.fspath(self.destination)))
        all_args.append(self.optional_named__init__param("unwtar", self.unwtar, "False"))

    def progress_msg_self(self) -> str:
        return f"""CopyBundle {os.fspath(self.source)} to '{os.fspath(self.destination)}'"""

    def __call__(self, *args, **kwargs) -> None:
        with CopyDirToDir(self.source, self.destination, link_dest=self.link_dest, ignore_patterns=self.ignore_patterns) as cdtd:
            cdtd()


class CopyGlobToDir(RsyncClone, kwargs_defaults={"only_files": True}):
    def __init__(self, glob_pattern, src, dst, **kwargs):
        super().__init__(src, dst, **kwargs)
        self.glob_pattern = glob_pattern

    def repr_own_args(self, all_args: List[str]) -> None:
        all_args.append(self.unnamed__init__param(self.glob_pattern))
        super().repr_own_args(all_args)

    def __call__(self, *args, **kwargs) -> None:
        resolved_source_dir: Path = utils.ExpandAndResolvePath(self.src)
        resolved_destination_dir: Path = utils.ExpandAndResolvePath(self.dst)
        globed_files =  list(resolved_source_dir.glob(self.glob_pattern))
        if globed_files:
            resolved_destination_dir.mkdir(parents=True, exist_ok=True)
            kwargs = self.all_kwargs_dict()
            kwargs['own_progress_count'] = 0
            for globed_file in globed_files:
                if globed_file.is_file():
                    with CopyFileToDir(globed_file, resolved_destination_dir, **kwargs) as copier:
                        copier()
                elif not self.only_files:
                    with CopyDirToDir(globed_file, resolved_destination_dir, **kwargs) as copier:
                        copier()
