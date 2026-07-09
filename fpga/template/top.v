module top #(
    parameter DATA_BITS = 8,
    parameter ADDRESS_BITS = 16,
    parameter IO_BITS = 2,
) (
    input wire nWR,
    input wire nRD,
    input wire [ADDRESS_BITS-1:0] ADDRESS,
    inout wire [DATA_BITS-1:0] DATA,
    output wire nRST,
    output wire VIN,
    output wire DIR_DATA,
    inout wire SPI_SCK,
    inout wire SPI_MOSI,
    inout wire SPI_MISO,
    inout wire SPI_SS,
    inout wire [IO_BITS-1:0] IO
);

wire clk;

wire [7:0] data;

SB_HFOSC #(
    .CLKHF_DIV("0b00")
) u_osc (
    .CLKHFPU(1'b1),
    .CLKHFEN(1'b1),
    .CLKHF(clk)
);

endmodule