// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
#ifndef __linux__

#include "xstatus.h"
#include "xparameters.h"
#include "xframe_diff.h"

extern XFrame_diff_Config XFrame_diff_ConfigTable[];

XFrame_diff_Config *XFrame_diff_LookupConfig(u16 DeviceId) {
	XFrame_diff_Config *ConfigPtr = NULL;

	int Index;

	for (Index = 0; Index < XPAR_XFRAME_DIFF_NUM_INSTANCES; Index++) {
		if (XFrame_diff_ConfigTable[Index].DeviceId == DeviceId) {
			ConfigPtr = &XFrame_diff_ConfigTable[Index];
			break;
		}
	}

	return ConfigPtr;
}

int XFrame_diff_Initialize(XFrame_diff *InstancePtr, u16 DeviceId) {
	XFrame_diff_Config *ConfigPtr;

	Xil_AssertNonvoid(InstancePtr != NULL);

	ConfigPtr = XFrame_diff_LookupConfig(DeviceId);
	if (ConfigPtr == NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}

	return XFrame_diff_CfgInitialize(InstancePtr, ConfigPtr);
}

#endif

