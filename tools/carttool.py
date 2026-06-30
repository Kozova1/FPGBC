import argparse
import time

import hid
import mcp2210

from loguru import logger
from mcp2210 import Mcp2210GpioDirection, Mcp2210GpioDesignation

from .types import hex_integer

PROGRAM_NAME = "carttool.py"

USB_VID = 0x1d50
USB_PID = 0x6267

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

MANUFACTURER_NAME = "Devboard Industries".encode("UTF-16-LE")
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
    _spi: mcp2210.Mcp2210
    _serial_number: str

    def __init__(self, serial_number: str):
        self._serial_number = serial_number
        self._spi = mcp2210.Mcp2210(
            serial_number=self._serial_number,
            vendor_id=USB_VID,
            product_id=USB_PID
        )

    @staticmethod
    def list_serials() -> list[str]:
        return [
            dev["serial_number"]
            for dev in hid.enumerate(vendor_id=USB_VID, product_id=USB_PID)
        ]

    def program(self, image: bytes):
        logger.info("Configuring MCP2210 GPIO/SPI for programming the FPGA")
        self._spi.set_spi_mode(3)
        self._spi.set_gpio_direction(SS_PIN_NUMBER, Mcp2210GpioDirection.OUTPUT)
        self._spi.set_gpio_direction(RESET_PIN_NUMBER, Mcp2210GpioDirection.OUTPUT)
        self._spi.set_gpio_direction(CDONE_PIN_NUMBER, Mcp2210GpioDirection.OUTPUT)
        self._spi.set_gpio_designation(SS_PIN_NUMBER, Mcp2210GpioDesignation.GPIO)
        self._spi.set_gpio_designation(RESET_PIN_NUMBER, Mcp2210GpioDesignation.GPIO)
        self._spi.set_gpio_designation(CDONE_PIN_NUMBER, Mcp2210GpioDesignation.GPIO)

        logger.info("Resetting the FPGA...")
        self._spi.set_gpio_output_value(RESET_PIN_NUMBER, False)
        self._spi.set_gpio_output_value(SS_PIN_NUMBER, False)
        time.sleep(1e-3)

        logger.info("Reenabling FPGA")
        self._spi.set_gpio_output_value(RESET_PIN_NUMBER, True)
        time.sleep(1e-3)

        logger.info("Unlocking SS pin - entering FPGA slave configuration mode")
        self._spi.set_gpio_output_value(SS_PIN_NUMBER, True)
        # self._spi.set_gpio_designation(SS_PIN_NUMBER, Mcp2210GpioDesignation.CHIP_SELECT)

        logger.info("Writing image to FPGA...")
        self._spi.set_gpio_output_value(SS_PIN_NUMBER, False)
        self._spi.spi_exchange(image, SS_PIN_NUMBER)
        self._spi.set_gpio_output_value(SS_PIN_NUMBER, True)
        time.sleep(1e-3)

        if self._spi.get_gpio_value(CDONE_PIN_NUMBER):
            logger.success("CDONE went high, programming succeeded")
        else:
            logger.critical("CDONE still low, programming failed")


def mcp2210_list(args: argparse.Namespace):
    devs = list(hid.enumerate(args.vendor_id, args.product_id))
    if not devs:
        logger.warning("No MCP2210 devices found")
        return

    for dev in devs:
        print(dev["serial_number"])

def mcp2210_program(args: argparse.Namespace):
    assert len(MANUFACTURER_NAME) < (63-6)
    assert len(DEVICE_NAME) < (63-6)
    logger.info(f"Connecting to MCP2210 with USB VID:PID:SERIAL = {args.vendor_id}:{args.product_id}:{args.serial_number}")
    mcp = mcp2210.Mcp2210(
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

def cart_list(args: argparse.Namespace):
    for cart_serial in Cartridge.list_serials():
        print(cart_serial)

def load_fpga(args: argparse.Namespace):
    pass

def get_args():
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME)
    subparsers = parser.add_subparsers(dest="cmd")
    subparsers.required = True

    mcp2210_parser = subparsers.add_parser("mcp2210")
    mcp2210_parser.add_argument("--vendor-id", type=hex_integer, help=f"USB VID in hex (0x{USB_VID:04X} by default)", default=USB_VID, dest="vendor_id")
    mcp2210_parser.add_argument("--product-id", type=hex_integer, help=f"USB PID in hex (0x{USB_PID:04x} by default)", default=USB_PID, dest="product_id")
    mcp2210_subparsers = mcp2210_parser.add_subparsers(dest="mcp2210_cmd")
    mcp2210_subparsers.required = True
    mcp2210_list_parser = mcp2210_subparsers.add_parser("list")
    mcp2210_list_parser.set_defaults(main_func=mcp2210_list)
    mcp2210_program_parser = mcp2210_subparsers.add_parser("program")
    mcp2210_program_parser.add_argument("--serial-number", type=str, required=True, dest="serial_number")
    mcp2210_program_parser.set_defaults(main_func=mcp2210_program)

    cart_parser = subparsers.add_parser("cart")
    cart_parser.add_argument("--serial-number", type=str, default=None, dest="serial_number")
    cart_subparsers = cart_parser.add_subparsers(dest="cart_cmd")
    cart_subparsers.required = True
    cart_list_parser = cart_subparsers.add_parser("list")
    cart_list_parser.set_defaults(main_func=cart_list)


    load_fpga_parser = cart_subparsers.add_parser('load-fpga')
    load_fpga_parser.set_defaults(main_func=load_fpga)
    load_fpga_parser.add_argument("--serial-number", required=True)

    args = parser.parse_args()
    return args


def main():
    args = get_args()
    args.main_func(args)


if __name__ == "__main__":
    main()
