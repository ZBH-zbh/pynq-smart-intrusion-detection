# Grove Buzzer GPIO 约束
# 默认把 AXI GPIO 的 bit0 接到 Grove Base Shield G1 口的信号线（G1=D2=AR2=U13）。
# 如果你插的是别的 G 口，改成对应的 PACKAGE_PIN 即可。

set_property -dict {PACKAGE_PIN U13 IOSTANDARD LVCMOS33} [get_ports {buzzer_pin[0]}];

# ---- 备用引脚对照表（PYNQ-Z2 Arduino 排针 -> Zynq PL 管脚）----
# AR0 (D0)  = T14
# AR1 (D1)  = U12
# AR2 (D2)  = U13
# AR3 (D3)  = V13
# AR4 (D4)  = V15
# AR5 (D5)  = T15
# AR6 (D6)  = R16
# AR7 (D7)  = U17
# AR8 (D8)  = V17
# AR9 (D9)  = V18
# AR10(D10) = T16
# AR11(D11) = R17
# AR12(D12) = P18
# AR13(D13) = N17
