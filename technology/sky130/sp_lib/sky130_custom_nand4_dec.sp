* NGSPICE file created from sky130_fd_bd_sram__openram_sp_custom_nand4_dec.ext - technology: sky130A

.subckt sky130_custom_nand4_dec A B C D Z VDD GND
X0 VDD C Z VDD sky130_fd_pr__pfet_01v8 w=1.12 l=0.15
X1 a_n384_98# C a_128_208# GND sky130_fd_pr__nfet_01v8 w=0.74 l=0.15
X2 Z D VDD VDD sky130_fd_pr__pfet_01v8 w=1.12 l=0.15
X3 Z B VDD VDD sky130_fd_pr__pfet_01v8 w=1.12 l=0.15
X4 a_128_136# A Z GND sky130_fd_pr__nfet_01v8 w=0.74 l=0.15
X5 VDD A Z VDD sky130_fd_pr__pfet_01v8 w=1.12 l=0.15
X6 a_128_208# B a_128_136# GND sky130_fd_pr__nfet_01v8 w=0.74 l=0.15
X7 GND D a_n384_98# GND sky130_fd_pr__nfet_01v8 w=0.74 l=0.15
.ends
