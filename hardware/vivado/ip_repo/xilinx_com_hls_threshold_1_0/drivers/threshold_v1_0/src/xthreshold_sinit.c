// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#include "xparameters.h"
#include "xthreshold.h"

extern XThreshold_Config XThreshold_ConfigTable[];

XThreshold_Config *XThreshold_LookupConfig(u16 DeviceId) {
	XThreshold_Config *ConfigPtr = NULL;

	int Index;

	for (Index = 0; Index < XPAR_XTHRESHOLD_NUM_INSTANCES; Index++) {
		if (XThreshold_ConfigTable[Index].DeviceId == DeviceId) {
			ConfigPtr = &XThreshold_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XThreshold_Initialize(XThreshold *InstancePtr, u16 DeviceId) {
	XThreshold_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XThreshold_LookupConfig(DeviceId);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XThreshold_CfgInitialize(InstancePtr, ConfigPtr);
}

#endif

