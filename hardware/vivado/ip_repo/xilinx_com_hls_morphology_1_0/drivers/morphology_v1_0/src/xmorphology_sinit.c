// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#include "xparameters.h"
#include "xmorphology.h"

extern XMorphology_Config XMorphology_ConfigTable[];

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

