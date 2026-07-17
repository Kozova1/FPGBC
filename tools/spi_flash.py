from contextlib import contextmanager
import functools

from collections.abc import Callable, Generator
import time
from typing import get_type_hints, Any, overload
from mcp2210.mcp2210 import Mcp2210


def command[R](data: bytes, response_length: int = 0, type: Callable[[bytes], R] = lambda _: None) -> Callable[[callable], Callable[[W25Q128JVS], R]]:
    def bound_command(func: callable) -> Callable[[W25Q128JVS], R]:
        @functools.wraps(func)
        def fn(self: W25Q128JVS) -> R:
            res = self._exchange(data + b"\x00" * response_length)[len(data) - response_length - 1:]
            return type(res)
        return fn
    return bound_command

def int8(data: bytes) -> int:
    assert len(data) == 1
    return data[0]

def int16be(data: bytes) -> int:
    assert len(data) == 2
    return int.from_bytes(data, "big")

def int64be(data: bytes) -> int:
    assert len(data) == 8
    return int.from_bytes(data, "big")

class W25Q128JVS:
    PAGE_SIZE = 0x100
    SECTOR_SIZE = 0x1000
    MEDIUM_SECTOR_SIZE = 0x8000
    LARGE_SECTOR_SIZE = 0x10000

    def __init__(self, spi: Mcp2210, pin: int):
        self.spi = spi
        self.cs_pin = pin
    
    def _exchange(self, cmd: bytes) -> bytes:
        return self.spi.spi_exchange(cmd, self.cs_pin)

    @overload
    def __getitem__(self, address: int) -> bytes: ...

    @overload
    def __getitem__(self, start: slice) -> bytes: ...
    
    def __getitem__(self, address: int | slice) -> int | bytes:
        if isinstance(address, int):
            return self.read_data(address)
        
        start, stop = address.start, address.stop

        assert address.step in {1, None}, "slice must be contiguous"
        assert start <= stop, "stop address must be higher than start address"

        self._format_address(start)
        self._format_address(stop)
        
        return self.fast_read(start, stop - start)

    def __setitem__(self, address: int, data: bytes):
        self.page_program(address, data)

    @staticmethod
    def _format_address(address: int) -> bytes:
        return address.to_bytes(3, "big")

    def read_data(self, address: int) -> int:
        addr = self._format_address(address)
        return self._exchange(b"\x03" + addr + b"\x00")[4]
    
    def fast_read(self, address: int, count: int) -> bytes:
        addr = self._format_address(address)
        return self._exchange(b"\x0B" + addr + b"\x00" + b"\x00" * count)[5:]
    
    def erase_sector(self, address: int):
        addr = self._format_address(address)
        self._exchange(b"\x20" + addr)
    
    def erase_medium_sector(self, address: int):
        addr = self._format_address(address)
        self._exchange(b"\x52" + addr)
    
    def erase_large_sector(self, address: int):
        addr = self._format_address(address)
        self._exchange(b"\xD8" + addr)

    def erase_range(self, start: int, stop: int) -> Generator[int, None, None]:
        self._format_address(start)
        self._format_address(stop)
        assert (start % self.SECTOR_SIZE) == 0, f"range start must be aligned to a sector ({self.SECTOR_SIZE} bytes)"
        assert (stop % self.SECTOR_SIZE) == 0, f"range stop must be aligned to a sector ({self.SECTOR_SIZE} bytes)"

        current = start
        while current < stop:
            with self.single_write():
                if ((current % self.LARGE_SECTOR_SIZE) == 0) and ((current + self.LARGE_SECTOR_SIZE) <= stop):
                    self.erase_large_sector(current)
                    yield self.LARGE_SECTOR_SIZE
                    current += self.LARGE_SECTOR_SIZE
                elif ((current % self.MEDIUM_SECTOR_SIZE) == 0) and ((current + self.MEDIUM_SECTOR_SIZE) <= stop):
                    self.erase_medium_sector(current)
                    yield self.MEDIUM_SECTOR_SIZE
                    current += self.MEDIUM_SECTOR_SIZE
                else:
                    self.erase_sector(current)
                    yield self.SECTOR_SIZE
                    current += self.SECTOR_SIZE
    
    def program_range(self, start: int, image: bytes):
        stop = start + len(image)
        self._format_address(start)
        self._format_address(stop)
        assert (start % self.SECTOR_SIZE) == 0, f"range start must be aligned to a sector ({self.SECTOR_SIZE} bytes)"
        assert (stop % self.SECTOR_SIZE) == 0, f"image size must be aligned to a sector ({self.SECTOR_SIZE} bytes)"

        for i in range(0, len(image), self.PAGE_SIZE):
            page = image[i:i+self.PAGE_SIZE]
            if set(page) != {0xFF}:
                with self.single_write():
                    self[i] = image[i:i+self.PAGE_SIZE]
            yield self.PAGE_SIZE
    
    @contextmanager
    def single_write(self):
        self.write_enable()
        yield
        self.wait_for_program()
    
    def page_program(self, address: int, data: bytes):
        assert len(data) == 256, "programming is only supported for whole pages"
        assert (address % self.PAGE_SIZE) == 0, "addresses must be page-aligned"
        addr = self._format_address(address)
        self._exchange(b"\x02" + addr + data)

    @command(b"\xAB")
    def wakeup(self): pass

    @command(b"\xB9")
    def power_down(self): pass
    
    @command(b"\x06")
    def write_enable(self): pass
    
    @command(b"\x50")
    def write_enable_vsr(self): pass
    
    @command(b"\x04")
    def write_disable(self): pass

    @property
    @command(b"\x05", response_length=1, type=int8)
    def sr1(self): pass

    @sr1.setter
    def write_sr1(self, data: int):
        self._exchange(b"\x01" + data.to_bytes(1, "big"))

    @property
    @command(b"\x35", response_length=1, type=int8)
    def sr2(self): pass

    @sr2.setter
    def write_sr2(self, data: int):
        self._exchange(b"\x31" + data.to_bytes(1, "big"))

    @property
    @command(b"\x15", response_length=1, type=int8)
    def sr3(self): pass

    @sr3.setter
    def write_sr3(self, data: int):
        self._exchange(b"\x11" + data.to_bytes(1, "big"))


    @property
    @command(b"\x90\x00\x00\x00", response_length=2, type=tuple)
    def manufacturer_and_device_id(self): pass

    @property
    @command(b"\xAB\x00\x00\x00", response_length=1, type=int8)
    def device_id(self): pass

    @property
    @command(b"\x4B\x00\x00\x00\x00", response_length=8, type=int64be)
    def unique_id(self): pass

    @property
    @command(b"\x9F", response_length=3, type=tuple)
    def jedec_id(self): pass

    @command(b"\xC7")
    def chip_erase(self): pass

    def wait_for_program(self, output: bool = False):
        i = 0
        while (self.sr1 & 1) != 0:
            time.sleep(1)
            i += 1
            if output:
                print(".", end='', flush=True)
        
        if output:
            print()

    @command(b"\x75")
    def suspend_operation(self): pass

    @command(b"\x7A")
    def resume_operation(self): pass

    def read_sfdp(self, address: int) -> bytes:
        assert (address >> 8) == 0, "address must be 24-bit with top 16 bits zeroed"
        res = self._exchange(b"\x5A" + self._format_address(address) + b"\x00\x00\x00")
        return int16be(res[5:])

    @staticmethod
    def _format_security_register_address(address: int) -> bytes:
        assert (address & 0xFFCF00) == 0, "address bits [23:14] and [11:8] must be 0"
        assert (address >> 24) == 0, "address must be up to 24 bits"
        assert ((address >> 12) & 0b11) != 0, "address bits [12:13] cannot be 0"
        return address.to_bytes(3, "big")

    def erase_security_regs(self, address: int):
        addr = self._format_security_register_address(address)
        self._exchange(b"\x44" + addr)
    
    def program_security_regs(self, address: int, data: bytes):
        addr = self._format_security_register_address(address)
        self._exchange(b"\x42" + addr + data)
    
    def read_security_regs(self, address: int, count: int) -> bytes:
        addr = self._format_security_register_address(address)
        return self._exchange(b"\x48" + addr + b"\x00" + b"\x00" * count)[5:]
    
    def lock_block(self, address: int):
        self._exchange(b"\x36" + self._format_address(address))
    
    def unlock_block(self, address: int):
        self._exchange(b"\x36" + self._format_address(address))
    
    def is_block_locked(self, address: int) -> bool:
        res = self._exchange(b"\x3D" + self._format_address(address) + b"\x00")
        return bool(res[4] & 1)
    
    @command(b"\x98")
    def global_block_unlock(self): pass

    def reset(self):
        self._exchange(b"\x66")
        self._exchange(b"\x99")



