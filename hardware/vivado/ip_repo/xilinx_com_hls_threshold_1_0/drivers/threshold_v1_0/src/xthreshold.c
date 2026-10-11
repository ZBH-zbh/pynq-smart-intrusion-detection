// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
/***************************** Include Files *********************************/
#include "xthreshold.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XThreshold_CfgInitialize(XThreshold *InstancePtr, XThreshold_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Ctrl_BaseAddress = ConfigPtr->Ctrl_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XThreshold_Start(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL) & 0x80;
    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XThreshold_IsDone(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XThreshold_IsIdle(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XThreshold_IsReady(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XThreshold_EnableAutoRestart(XThreshold *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL, 0x80);
}

void XThreshold_DisableAutoRestart(XThreshold *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_AP_CTRL, 0);
}

void XThreshold_Set_width(XThreshold *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_WIDTH_DATA, Data);
}

u32 XThreshold_Get_width(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_WIDTH_DATA);
    return Data;
}

void XThreshold_Set_height(XThreshold *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_HEIGHT_DATA, Data);
}

u32 XThreshold_Get_height(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_HEIGHT_DATA);
    return Data;
}

void XThreshold_Set_thresh(XThreshold *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_THRESH_DATA, Data);
}

u32 XThreshold_Get_thresh(XThreshold *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_THRESH_DATA);
    return Data;
}

void XThreshold_InterruptGlobalEnable(XThreshold *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_GIE, 1);
}

void XThreshold_InterruptGlobalDisable(XThreshold *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_GIE, 0);
}

void XThreshold_InterruptEnable(XThreshold *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_IER);
    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_IER, Register | Mask);
}

void XThreshold_InterruptDisable(XThreshold *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_IER);
    XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_IER, Register & (~Mask));
}

void XThreshold_InterruptClear(XThreshold *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    //XThreshold_WriteReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_ISR, Mask);
}

u32 XThreshold_InterruptGetEnabled(XThreshold *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_IER);
}

u32 XThreshold_InterruptGetStatus(XThreshold *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    // Current Interrupt Clear Behavior is Clear on Read(COR).
    return XThreshold_ReadReg(InstancePtr->Ctrl_BaseAddress, XTHRESHOLD_CTRL_ADDR_ISR);
}

