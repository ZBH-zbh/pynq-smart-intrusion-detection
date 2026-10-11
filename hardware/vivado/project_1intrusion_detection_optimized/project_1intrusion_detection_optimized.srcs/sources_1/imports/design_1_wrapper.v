//Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
//--------------------------------------------------------------------------------
//Tool Version: Vivado v.2022.1 (win64) Build 3526262 Mon Apr 18 15:48:16 MDT 2022
//Date        : Sat Sep 12 23:04:18 2026
//Host        : LAPTOP-90MB5P75 running 64-bit major release  (build 9200)
//Command     : generate_target design_1_wrapper.bd
//Design      : design_1_wrapper
//Purpose     : IP block netlist
//--------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

module design_1_wrapper
   (buzzer_pin);
  output [0:0]buzzer_pin;

  wire [0:0]buzzer_pin;

  design_1 design_1_i
       (.buzzer_pin(buzzer_pin));
endmodule
