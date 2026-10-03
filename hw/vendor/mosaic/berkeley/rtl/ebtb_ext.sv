// extracted from chipyard.harness.TestHarness.MosaicRocketBoomConfig.top.mems.v

module ebtb_ext(
  input  [6:0]  R0_addr,
  input         R0_clk,
  output [39:0] R0_data,
  input         R0_en,
  input  [6:0]  W0_addr,
  input         W0_clk,
  input  [39:0] W0_data,
  input         W0_en
);
  wire [6:0] mem_0_0_R0_addr;
  wire  mem_0_0_R0_clk;
  wire [39:0] mem_0_0_R0_data;
  wire  mem_0_0_R0_en;
  wire [6:0] mem_0_0_W0_addr;
  wire  mem_0_0_W0_clk;
  wire [39:0] mem_0_0_W0_data;
  wire  mem_0_0_W0_en;
  split_ebtb_ext mem_0_0 (
    .R0_addr(mem_0_0_R0_addr),
    .R0_clk(mem_0_0_R0_clk),
    .R0_data(mem_0_0_R0_data),
    .R0_en(mem_0_0_R0_en),
    .W0_addr(mem_0_0_W0_addr),
    .W0_clk(mem_0_0_W0_clk),
    .W0_data(mem_0_0_W0_data),
    .W0_en(mem_0_0_W0_en)
  );
  assign R0_data = mem_0_0_R0_data;
  assign mem_0_0_R0_addr = R0_addr;
  assign mem_0_0_R0_clk = R0_clk;
  assign mem_0_0_R0_en = R0_en;
  assign mem_0_0_W0_addr = W0_addr;
  assign mem_0_0_W0_clk = W0_clk;
  assign mem_0_0_W0_data = W0_data;
  assign mem_0_0_W0_en = W0_en;
endmodule
