"""Windows 專用介面：資料夾連選、開啟資料夾。"""

from __future__ import annotations

import ctypes
import os
from ctypes import HRESULT, POINTER, WINFUNCTYPE, byref, c_void_p, c_wchar_p, wintypes
from pathlib import Path

FOS_PICKFOLDERS = 0x20
FOS_FORCEFILESYSTEM = 0x40
FOS_ALLOWMULTISELECT = 0x200
SIGDN_FILESYSPATH = 0x80058000
CLSCTX_INPROC_SERVER = 1
CANCELLED = 0x800704C7


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


def _guid(text: str) -> GUID:
    value = GUID()
    if ctypes.windll.ole32.CLSIDFromString(text, byref(value)) != 0:
        raise OSError(f"無法建立 GUID：{text}")
    return value


def _method(dialog, index, restype, *argtypes):
    table = ctypes.cast(dialog, POINTER(POINTER(c_void_p)))[0]
    return WINFUNCTYPE(restype, c_void_p, *argtypes)(table[index])


def pick_folders(owner: int = 0, title: str = "按住 Ctrl 或 Shift 連選資料夾") -> list[Path]:
    """開啟可複選的資料夾視窗。取消時回傳空清單。"""
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize.argtypes = [c_void_p]
    ole32.CoInitialize.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID),
        c_void_p,
        wintypes.DWORD,
        POINTER(GUID),
        POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoUninitialize.argtypes = []
    started = int(ole32.CoInitialize(None))
    if started not in (0, 1):
        raise OSError("無法初始化資料夾選擇視窗")
    dialog = c_void_p()
    try:
        class_id = _guid("{DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7}")
        interface_id = _guid("{D57C7288-D4AD-4768-BE02-9D969532D960}")
        hr = ole32.CoCreateInstance(
            byref(class_id),
            None,
            CLSCTX_INPROC_SERVER,
            byref(interface_id),
            byref(dialog),
        )
        if int(hr) != 0 or not dialog:
            raise OSError("無法建立資料夾選擇視窗")
        options = wintypes.DWORD()
        _method(dialog, 10, HRESULT, POINTER(wintypes.DWORD))(dialog, byref(options))
        flags = options.value | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_ALLOWMULTISELECT
        _method(dialog, 9, HRESULT, wintypes.DWORD)(dialog, flags)
        _method(dialog, 17, HRESULT, c_wchar_p)(dialog, title)
        shown = int(_method(dialog, 3, HRESULT, wintypes.HWND)(dialog, owner))
        if shown & 0xFFFFFFFF == CANCELLED:
            return []
        if shown != 0:
            raise OSError("資料夾選擇視窗沒有完成")
        array = c_void_p()
        result = int(_method(dialog, 27, HRESULT, POINTER(c_void_p))(dialog, byref(array)))
        if result != 0 or not array:
            raise OSError("讀不到選取的資料夾")
        try:
            return _item_paths(array)
        finally:
            _method(array, 2, wintypes.ULONG)(array)
    finally:
        if dialog:
            _method(dialog, 2, wintypes.ULONG)(dialog)
        if started == 0:
            ole32.CoUninitialize()


def open_folder(path: Path) -> None:
    os.startfile(Path(path))  # noqa: S606


def _item_paths(array) -> list[Path]:
    count = wintypes.DWORD()
    _method(array, 7, HRESULT, POINTER(wintypes.DWORD))(array, byref(count))
    paths: list[Path] = []
    for index in range(count.value):
        item = c_void_p()
        _method(array, 8, HRESULT, wintypes.DWORD, POINTER(c_void_p))(array, index, byref(item))
        if not item:
            continue
        try:
            name = c_wchar_p()
            _method(item, 5, HRESULT, wintypes.DWORD, POINTER(c_wchar_p))(item, SIGDN_FILESYSPATH, byref(name))
            if name.value:
                paths.append(Path(name.value))
                ctypes.windll.ole32.CoTaskMemFree(name)
        finally:
            _method(item, 2, wintypes.ULONG)(item)
    return paths
