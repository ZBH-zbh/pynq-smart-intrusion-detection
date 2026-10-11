// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
/***************************** Include Files *********************************/
#include "xmorphology.h"

/************************** Function Implementation *************************/
#ifndef __linux__
int XMorphology_CfgInitialize(XMorphology *InstancePtr, XMorphology_Config *ConfigPtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(ConfigPtr != NULL);

    InstancePtr->Ctrl_BaseAddress = ConfigPtr->Ctrl_BaseAddress;
    InstancePtr->IsReady = XIL_COMPONENT_IS_READY;

    return XST_SUCCESS;
}
#endif

void XMorphology_Start(XMorphology *InstancePtr) {
    u32 Data;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL) & 0x80;
    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL, Data | 0x01);
}

u32 XMorphology_IsDone(XMorphology *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL);
    return (Data >> 1) & 0x1;
}

u32 XMorphology_IsIdle(XMorphology *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL);
    return (Data >> 2) & 0x1;
}

u32 XMorphology_IsReady(XMorphology *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL);
    // check ap_start to see if the pcore is ready for next input
    return !(Data & 0x1);
}

void XMorphology_EnableAutoRestart(XMorphology *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL, 0x80);
}

void XMorphology_DisableAutoRestart(XMorphology *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_AP_CTRL, 0);
}

void XMorphology_Set_width(XMorphology *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_WIDTH_DATA, Data);
}

u32 XMorphology_Get_width(XMorphology *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_WIDTH_DATA);
    return Data;
}

void XMorphology_Set_height(XMorphology *InstancePtr, u32 Data) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_HEIGHT_DATA, Data);
}

u32 XMorphology_Get_height(XMorphology *InstancePtr) {
    u32 Data;

    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Data = XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_HEIGHT_DATA);
    return Data;
}

void XMorphology_InterruptGlobalEnable(XMorphology *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_GIE, 1);
}

void XMorphology_InterruptGlobalDisable(XMorphology *InstancePtr) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_GIE, 0);
}

void XMorphology_InterruptEnable(XMorphology *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_IER);
    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_IER, Register | Mask);
}

void XMorphology_InterruptDisable(XMorphology *InstancePtr, u32 Mask) {
    u32 Register;

    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    Register =  XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_IER);
    XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_IER, Register & (~Mask));
}

void XMorphology_InterruptClear(XMorphology *InstancePtr, u32 Mask) {
    Xil_AssertVoid(InstancePtr != NULL);
    Xil_AssertVoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    //XMorphology_WriteReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_ISR, Mask);
}

u32 XMorphology_InterruptGetEnabled(XMorphology *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    return XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_IER);
}

u32 XMorphology_InterruptGetStatus(XMorphology *InstancePtr) {
    Xil_AssertNonvoid(InstancePtr != NULL);
    Xil_AssertNonvoid(InstancePtr->IsReady == XIL_COMPONENT_IS_READY);

    // Current Interrupt Clear Behavior is Clear on Read(COR).
    return XMorphology_ReadReg(InstancePtr->Ctrl_BaseAddress, XMORPHOLOGY_CTRL_ADDR_ISR);
}

