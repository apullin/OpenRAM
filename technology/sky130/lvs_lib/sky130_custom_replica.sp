* SPICE3 file created from sky130_custom_replica.ext - technology: sky130A

.subckt sky130_custom_replica BL BR WL VPWR VGND
X0 BL WL Q VGND sky130_fd_pr__nfet_01v8 ad=0.1428p pd=1.52u as=0.0756p ps=0.78u w=0.42u l=0.15u
X1 VGND Q VPWR VGND sky130_fd_pr__nfet_01v8 ad=0.09795p pd=0.915u as=0.0777p ps=0.79u w=0.42u l=0.15u
X2 VPWR WL BR VGND sky130_fd_pr__nfet_01v8 ad=0.0777p pd=0.79u as=0.1218p ps=1.42u w=0.42u l=0.15u
X3 VPWR Q VPWR VPWR sky130_fd_pr__pfet_01v8 ad=0.0882p pd=0.84u as=0.2961p ps=3.09u w=0.42u l=0.15u
X4 Q VPWR VPWR VPWR sky130_fd_pr__pfet_01v8 ad=0.1218p pd=1.42u as=0.0882p ps=0.84u w=0.42u l=0.15u
X5 Q VPWR VGND VGND sky130_fd_pr__nfet_01v8 ad=0.0756p pd=0.78u as=0.09795p ps=0.915u w=0.42u l=0.15u
.ends
