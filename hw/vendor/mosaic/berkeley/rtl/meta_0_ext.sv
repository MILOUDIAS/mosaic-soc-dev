// extracted from chipyard.harness.TestHarness.MosaicRocketBoomConfig.top.mems.v

module meta_0_ext(
  input  [6:0]   R0_addr,
  input          R0_clk,
  output [123:0] R0_data,
  input          R0_en,
  input  [6:0]   W0_addr,
  input          W0_clk,
  input  [123:0] W0_data,
  input          W0_en,
  input  [3:0]   W0_mask
);
  wire [6:0] mem_0_0_R0_addr;
  wire  mem_0_0_R0_clk;
  wire [30:0] mem_0_0_R0_data;
  wire  mem_0_0_R0_en;
  wire [6:0] mem_0_0_W0_addr;
  wire  mem_0_0_W0_clk;
  wire [30:0] mem_0_0_W0_data;
  wire  mem_0_0_W0_en;
  wire  mem_0_0_W0_mask;
  wire [6:0] mem_0_1_R0_addr;
  wire  mem_0_1_R0_clk;
  wire [30:0] mem_0_1_R0_data;
  wire  mem_0_1_R0_en;
  wire [6:0] mem_0_1_W0_addr;
  wire  mem_0_1_W0_clk;
  wire [30:0] mem_0_1_W0_data;
  wire  mem_0_1_W0_en;
  wire  mem_0_1_W0_mask;
  wire [6:0] mem_0_2_R0_addr;
  wire  mem_0_2_R0_clk;
  wire [30:0] mem_0_2_R0_data;
  wire  mem_0_2_R0_en;
  wire [6:0] mem_0_2_W0_addr;
  wire  mem_0_2_W0_clk;
  wire [30:0] mem_0_2_W0_data;
  wire  mem_0_2_W0_en;
  wire  mem_0_2_W0_mask;
  wire [6:0] mem_0_3_R0_addr;
  wire  mem_0_3_R0_clk;
  wire [30:0] mem_0_3_R0_data;
  wire  mem_0_3_R0_en;
  wire [6:0] mem_0_3_W0_addr;
  wire  mem_0_3_W0_clk;
  wire [30:0] mem_0_3_W0_data;
  wire  mem_0_3_W0_en;
  wire  mem_0_3_W0_mask;
  wire [30:0] R0_data_0_0 = mem_0_0_R0_data;
  wire [30:0] R0_data_0_1 = mem_0_1_R0_data;
  wire [30:0] R0_data_0_2 = mem_0_2_R0_data;
  wire [30:0] R0_data_0_3 = mem_0_3_R0_data;
  wire [92:0] _GEN_1 = {R0_data_0_2,R0_data_0_1,R0_data_0_0};
  split_meta_0_ext mem_0_0 (
    .R0_addr(mem_0_0_R0_addr),
    .R0_clk(mem_0_0_R0_clk),
    .R0_data(mem_0_0_R0_data),
    .R0_en(mem_0_0_R0_en),
    .W0_addr(mem_0_0_W0_addr),
    .W0_clk(mem_0_0_W0_clk),
    .W0_data(mem_0_0_W0_data),
    .W0_en(mem_0_0_W0_en),
    .W0_mask(mem_0_0_W0_mask)
  );
  split_meta_0_ext mem_0_1 (
    .R0_addr(mem_0_1_R0_addr),
    .R0_clk(mem_0_1_R0_clk),
    .R0_data(mem_0_1_R0_data),
    .R0_en(mem_0_1_R0_en),
    .W0_addr(mem_0_1_W0_addr),
    .W0_clk(mem_0_1_W0_clk),
    .W0_data(mem_0_1_W0_data),
    .W0_en(mem_0_1_W0_en),
    .W0_mask(mem_0_1_W0_mask)
  );
  split_meta_0_ext mem_0_2 (
    .R0_addr(mem_0_2_R0_addr),
    .R0_clk(mem_0_2_R0_clk),
    .R0_data(mem_0_2_R0_data),
    .R0_en(mem_0_2_R0_en),
    .W0_addr(mem_0_2_W0_addr),
    .W0_clk(mem_0_2_W0_clk),
    .W0_data(mem_0_2_W0_data),
    .W0_en(mem_0_2_W0_en),
    .W0_mask(mem_0_2_W0_mask)
  );
  split_meta_0_ext mem_0_3 (
    .R0_addr(mem_0_3_R0_addr),
    .R0_clk(mem_0_3_R0_clk),
    .R0_data(mem_0_3_R0_data),
    .R0_en(mem_0_3_R0_en),
    .W0_addr(mem_0_3_W0_addr),
    .W0_clk(mem_0_3_W0_clk),
    .W0_data(mem_0_3_W0_data),
    .W0_en(mem_0_3_W0_en),
    .W0_mask(mem_0_3_W0_mask)
  );
  assign R0_data = {R0_data_0_3,_GEN_1};
  assign mem_0_0_R0_addr = R0_addr;
  assign mem_0_0_R0_clk = R0_clk;
  assign mem_0_0_R0_en = R0_en;
  assign mem_0_0_W0_addr = W0_addr;
  assign mem_0_0_W0_clk = W0_clk;
  assign mem_0_0_W0_data = W0_data[30:0];
  assign mem_0_0_W0_en = W0_en;
  assign mem_0_0_W0_mask = W0_mask[0];
  assign mem_0_1_R0_addr = R0_addr;
  assign mem_0_1_R0_clk = R0_clk;
  assign mem_0_1_R0_en = R0_en;
  assign mem_0_1_W0_addr = W0_addr;
  assign mem_0_1_W0_clk = W0_clk;
  assign mem_0_1_W0_data = W0_data[61:31];
  assign mem_0_1_W0_en = W0_en;
  assign mem_0_1_W0_mask = W0_mask[1];
  assign mem_0_2_R0_addr = R0_addr;
  assign mem_0_2_R0_clk = R0_clk;
  assign mem_0_2_R0_en = R0_en;
  assign mem_0_2_W0_addr = W0_addr;
  assign mem_0_2_W0_clk = W0_clk;
  assign mem_0_2_W0_data = W0_data[92:62];
  assign mem_0_2_W0_en = W0_en;
  assign mem_0_2_W0_mask = W0_mask[2];
  assign mem_0_3_R0_addr = R0_addr;
  assign mem_0_3_R0_clk = R0_clk;
  assign mem_0_3_R0_en = R0_en;
  assign mem_0_3_W0_addr = W0_addr;
  assign mem_0_3_W0_clk = W0_clk;
  assign mem_0_3_W0_data = W0_data[123:93];
  assign mem_0_3_W0_en = W0_en;
  assign mem_0_3_W0_mask = W0_mask[3];
endmodule
