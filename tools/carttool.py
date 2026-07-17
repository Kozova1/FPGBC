import argparse
from itertools import batched
import math
from pathlib import Path
import time

import hid
import mcp2210

from loguru import logger
from mcp2210.mcp2210 import Mcp2210GpioDirection, Mcp2210GpioDesignation
from tqdm import tqdm, trange

from tools.spi_flash import W25Q128JVS

from .types import hex_integer
from .argparse_tree import Argument, LeafCommand, Command, make_parser

PROGRAM_NAME = "carttool.py"

USB_VID = 0x04D8
USB_PID = 0xE429

MCP2210_IO_CONFIG_COMMAND = bytes.fromhex(
    "60 20 00 00 " # Set Chip NVRAM Power-up Default
    "01 00 00 00 00 00 00 00 00 " # GP0 -> CSn, GP1-8 -> GPIO
    "03 00 " # CSn -> 1, RESETn -> 1, all others -> 0
    "FF 01 " # all -> input
    "00 00 " # No SPI bus release or protection
    "00 00 00 00 00 00 00 00 " # Do not set password
)

MCP2210_USB_ID_CONFIG_COMMAND = bytes.fromhex(
    "60 30 00 00 " # Set Chip NVRAM USB Power-up parameters
    f"{USB_VID & 0xFF:02X} {(USB_VID >> 8) & 0xFF:02X} " # USB VID
    f"{USB_PID & 0xFF:02X} {(USB_PID >> 8) & 0xFF:02X} " # USB PID
    "40 " # Self-powered (a lie, but the USB power circuitry is separated from the MCP2210)
    f"{250:02X} " # Request max current from the host
)

MANUFACTURER_NAME = "Vogman Devices".encode("UTF-16-LE")
DEVICE_NAME = "FPGBC".encode("UTF-16-LE")

MCP2210_USB_MANUFACTURER_CONFIG_COMMAND = bytes.fromhex(
    "60 50 00 00 " + # Set Chip NVRAM USB Manufacturer name
    f"{len(MANUFACTURER_NAME) * 2 + 2:02X} " + # Length(UTF16LE)*2+2 as per docs
    MANUFACTURER_NAME.hex(" ") + " "
)

MCP2210_USB_NAME_CONFIG_COMMAND = bytes.fromhex(
    "60 40 00 00" + # Set Chip NVRAM USB Product name
    f"{len(DEVICE_NAME) * 2 + 2:02X} " + # Length(UTF16LE)*2+2 as per docs
    DEVICE_NAME.hex(" ") + " "
)

SS_PIN_NUMBER = 0
RESET_PIN_NUMBER = 1
CDONE_PIN_NUMBER = 4

class Cartridge:
    _spi: mcp2210.mcp2210.Mcp2210
    _serial_number: str

    def __init__(self, serial_number: str):
        self._serial_number = serial_number
        self._spi = mcp2210.mcp2210.Mcp2210(
            serial_number=self._serial_number,
            vendor_id=USB_VID,
            product_id=USB_PID
        )

    @staticmethod
    def list_serials(vid: int = USB_VID, pid: int = USB_PID) -> list[str]:
        return [
            dev["serial_number"]
            for dev in hid.enumerate(vendor_id=vid, product_id=pid)
        ]

    def reset_fpga(self):
        self._spi.set_gpio_output_value(RESET_PIN_NUMBER, True)

    def program_flash(self, image: bytes, mass_erase: bool = False, verify_only: bool = False, skip_verify: bool = False):
        logger.info("Configuring MCP2210 GPIO/SPI for programming the flash")
        self._spi.set_spi_mode(3)
        self._spi.set_gpio_designation(SS_PIN_NUMBER, Mcp2210GpioDesignation.CHIP_SELECT)
        self._spi.set_gpio_designation(RESET_PIN_NUMBER, Mcp2210GpioDesignation.GPIO)
        self._spi.set_gpio_direction(RESET_PIN_NUMBER, Mcp2210GpioDirection.OUTPUT)
        logger.info("Resetting the FPGA...")
        self._spi.set_gpio_output_value(RESET_PIN_NUMBER, False)
        time.sleep(1e-3)
        flash = W25Q128JVS(self._spi, SS_PIN_NUMBER)
        flash.wakeup()
        logger.info(f"flash JEDEC ID: {flash.jedec_id}")

        if (len(image) % flash.SECTOR_SIZE) != 0:
            padding_count = len(image) % flash.SECTOR_SIZE
            logger.warning(f"Image not aligned to sector size ({flash.SECTOR_SIZE}). {padding_count} bytes after image will be erased and not programmed (left as FF)")
            image = image + (b"\xFF" * padding_count)

        if not verify_only:
            if mass_erase:
                flash.write_enable()
                flash.chip_erase()
                flash.wait_for_program(True)
            else:
                with tqdm(total=len(image), unit="B", unit_scale=True, unit_divisor=1024, desc="erasing flash") as pbar:
                    for step in flash.erase_range(0, len(image)):
                        pbar.update(step)
                    pbar.colour = "green"

        if not verify_only:
            with tqdm(total=len(image), unit="B", unit_scale=True, unit_divisor=1024, desc="programming flash") as pbar:
                for step in flash.program_range(0, image):
                    pbar.update(step)
                pbar.colour = "green"
        
        verification_success = True
        if not skip_verify:
            with tqdm(total=len(image), unit="B", unit_scale=True, unit_divisor=1024, desc="verifying flash") as pbar:
                for page_base in range(0, len(image), flash.PAGE_SIZE):
                    img = image[page_base:page_base+flash.PAGE_SIZE]
                    fls = flash[page_base:page_base+flash.PAGE_SIZE]
                    if img != fls:
                        logger.warning(f"mismatch at addresses {[
                            hex(page_base + j)
                            for j, (l, r) in enumerate(zip(img, fls))
                            if l != r
                        ]}")
                        pbar.colour = "red"
                        verification_success = False
                    pbar.update(flash.PAGE_SIZE)
            
                if verification_success:
                    pbar.colour = "green"
                    pbar.refresh()
                    logger.success("Finished programming")
                else:
                    logger.warning("Finished programming, flash doesn't match image")



    def program_fpga(self, image: bytes):
        logger.critical("Programming FPGA directly is not implemented using MCP2210")


def list_mcp2210(args: argparse.Namespace):
    devs = list(hid.enumerate(args.vendor_id, args.product_id))
    if not devs:
        logger.warning("No MCP2210 devices found")
        return

    for dev in devs:
        print(dev["serial_number"])

def program_mcp2210(args: argparse.Namespace):
    assert len(MANUFACTURER_NAME) < (63-6)
    assert len(DEVICE_NAME) < (63-6)
    logger.info(f"Connecting to MCP2210 with USB VID:PID:SERIAL = {args.vendor_id}:{args.product_id}:{args.serial_number}")
    mcp = mcp2210.mcp2210.Mcp2210(
        serial_number=args.serial_number,
        vendor_id=args.vendor_id,
        product_id=args.product_id,
    )
    logger.info("Connected to MCP2210")

    logger.info("Programming IO config...")
    response = mcp._execute_command(MCP2210_IO_CONFIG_COMMAND, pad_with_zeros=True, check_return_code=True)
    logger.debug(f"MCP2210 response: {response.hex(' ')}")

    logger.info(f"Programming USB VID:PID..")
    response = mcp._execute_command(MCP2210_USB_ID_CONFIG_COMMAND, pad_with_zeros=True, check_return_code=True)
    logger.debug(f"MCP2210 response: {response.hex(' ')}")

    logger.info(f"Programming USB manufacturer..")
    response = mcp._execute_command(MCP2210_USB_MANUFACTURER_CONFIG_COMMAND, pad_with_zeros=True, check_return_code=True)
    logger.debug(f"MCP2210 response: {response.hex(' ')}")

    logger.info(f"Programming USB device name..")
    response = mcp._execute_command(MCP2210_USB_NAME_CONFIG_COMMAND, pad_with_zeros=True, check_return_code=True)
    logger.debug(f"MCP2210 response: {response.hex(' ')}")

    logger.success(f"MCP2210 programming complete")

def list_carts(args: argparse.Namespace):
    for cart_serial in Cartridge.list_serials(vid=args.vendor_id, pid=args.product_id):
        print(cart_serial)

def program_fpga(args: argparse.Namespace):
    pass

def program_flash(args: argparse.Namespace):
    cart = Cartridge(args.serial_number)
    cart.program_flash(
        args.image.read_bytes(),
        mass_erase=args.mass_erase,
        verify_only=args.verify_only,
        skip_verify=args.skip_verify,
    )
    if args.reset_fpga:
        cart.reset_fpga()


def get_serial_number(args: argparse.Namespace) -> str | None:
    if args.serial_number is not None:
        return args.serial_number

    serials = Cartridge.list_serials(vid=args.vendor_id, pid=args.product_id)
    if not serials:
        logger.error("No serial numbers found")
        return None

    if len(serials) > 1:
        logger.error("Multiple serial numbers found:")
        for serial in serials:
            print(f"  {serial}")
        return None

    return serials[0]


def get_args_decl():
    desc = Command(
        {
            "list": Command(
                {
                    "cart": LeafCommand(
                        list_carts,
                        help="list connected carts",
                    ),
                    "mcp2210": LeafCommand(
                        list_mcp2210,
                        help="list connected MCP2210 devices",
                    )
                },
                help="list connected devices",
            ),
            "program": Command(
                {
                    "mcp2210": LeafCommand(
                        program_mcp2210,
                        help="program connected MCP2210 device with cartridge default settings",
                    ),
                    "fpga": LeafCommand(
                        program_fpga,
                        help="program cartridge FPGA ephemerally",
                    ),
                    "flash": LeafCommand(
                        program_flash,
                        help="program cartridge flash memory",
                        arguments=[
                            Argument("--image", required=True, type=Path, help="path to SPI flash image", dest="image"),
                            Argument("--mass-erase", action="store_true", help="use mass erase instead of localized erase", dest="mass_erase"),
                            Argument("--verify-only", action="store_true", help="only verify the flash instead of programming it", dest="verify_only"),
                            Argument("--do-not-verify", action="store_true", help="do not verify flash writes (dangerous!)", dest="skip_verify"),
                            Argument("--reset-fpga", action="store_true", help="reset the FPGA after flashing", dest="reset_fpga"),
                        ]
                    ),
                },
                help="program connected devices",
            ),
        },
        [
            Argument("--serial-number", dest="serial_number", default=None, help="the serial number of the MCP2210 (optional if only one is connected)"),
            Argument("--vendor-id", type=hex_integer, help=f"USB vendor ID in hex (0x{USB_VID:04X} by default)", default=USB_VID, dest="vendor_id"),
            Argument("--product-id", type=hex_integer, help=f"USB product ID in hex (0x{USB_PID:04x} by default)", default=USB_PID, dest="product_id"),
        ],
    )

    parser = make_parser(PROGRAM_NAME, desc)

    args = parser.parse_args()
    args.serial_number = get_serial_number(args)
    return args


def main():
    args = get_args_decl()

    if args.serial_number is not None:
        args.main_func(args)


if __name__ == "__main__":
    main()
