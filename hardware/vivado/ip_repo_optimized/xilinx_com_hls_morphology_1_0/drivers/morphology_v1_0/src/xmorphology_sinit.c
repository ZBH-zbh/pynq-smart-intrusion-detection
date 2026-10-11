// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2025.2 (64-bit)
// Tool Version Limit: 2025.11
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// Copyright 2022-2025 Advanced Micro Devices, Inc. All Rights Reserved.
// 
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#ifdef SDT
#include "xparameters.h"
#endif
#include "xmorphology.h"

extern XMorphology_Config XMorphology_ConfigTable[];

#ifdef SDT
XMorphology_Config *XMorphology_LookupConfig(UINTPTR BaseAddress) {
	XMorphology_Config *ConfigPtr = NULL;

	int Index;

	for (Index = (u32)0x0; XMorphology_ConfigTable[Index].Name != NULL; Index++) {
		if (!BaseAddress || XMorphology_ConfigTable[Index].Ctrl_BaseAddress == BaseAddress) {
			ConfigPtr = &XMorphology_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XMorphology_Initialize(XMorphology *InstancePtr, UINTPTR BaseAddress) {
	XMorphology_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XMorphology_LookupConfig(BaseAddress);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XMorphology_CfgInitialize(InstancePtr, ConfigPtr);
}
#else
XMorphology_Config *XMorphology_LookupConfig(u16 DeviceId) {
	XMorphology_Config *ConfigPtr = NULL;

	int Index;

	for (Index = 0; Index < XPAR_XMORPHOLOGY_NUM_INSTANCES; Index++) {
		if (XMorphology_ConfigTable[Index].DeviceId == DeviceId) {
			ConfigPtr = &XMorphology_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XMorphology_Initialize(XMorphology *InstancePtr, u16 DeviceId) {
	XMorphology_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XMorphology_LookupConfig(DeviceId);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XMorphology_CfgInitialize(InstancePtr, ConfigPtr);
}
#endif

#endif

