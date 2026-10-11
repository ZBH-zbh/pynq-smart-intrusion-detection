// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
/***************************** Include Files *********************************/
#include "xframe_diff.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XFrame_diff_CfgInitialize(XFrame_diff *InstancePtr, XFrame_diff_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Ctrl_BaseAddress = ConfigPtr->Ctrl_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XFrame_diff_Start(XFrame_diff *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL) & 0x80;
    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XFrame_diff_IsDone(XFrame_diff *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XFrame_diff_IsIdle(XFrame_diff *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XFrame_diff_IsReady(XFrame_diff *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XFrame_diff_EnableAutoRestart(XFrame_diff *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL, 0x80);
}

void XFrame_diff_DisableAutoRestart(XFrame_diff *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_AP_CTRL, 0);
}

void XFrame_diff_Set_width(XFrame_diff *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_WIDTH_DATA, Data);
}

u32 XFrame_diff_Get_width(XFrame_diff *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_WIDTH_DATA);
    return Data;
}

void XFrame_diff_Set_height(XFrame_diff *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_HEIGHT_DATA, Data);
}

u32 XFrame_diff_Get_height(XFrame_diff *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_HEIGHT_DATA);
    return Data;
}

void XFrame_diff_InterruptGlobalEnable(XFrame_diff *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_GIE, 1);
}

void XFrame_diff_InterruptGlobalDisable(XFrame_diff *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_GIE, 0);
}

void XFrame_diff_InterruptEnable(XFrame_diff *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_IER);
    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_IER, Register | Mask);
}

void XFrame_diff_InterruptDisable(XFrame_diff *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_IER);
    XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_IER, Register & (~Mask));
}

void XFrame_diff_InterruptClear(XFrame_diff *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    //XFrame_diff_WriteReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_ISR, Mask);
}

u32 XFrame_diff_InterruptGetEnabled(XFrame_diff *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_IER);
}

u32 XFrame_diff_InterruptGetStatus(XFrame_diff *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    // Current Interrupt Clear Behavior is Clear on Read(COR).
    return XFrame_diff_ReadReg(InstancePtr->Ctrl_BaseAddress, XFRAME_DIFF_CTRL_ADDR_ISR);
}

