// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
#ifndef XFRAME_DIFF_H
#define XFRAME_DIFF_H

#ifdef __cplusplus
extern "C" {
#endif

/***************************** Include Files *********************************/
#ifndef __linux__
#include "xil_types.h"
#include "xil_assert.h"
#include "xstatus.h"
#include "xil_io.h"
#else
#include <stdint.h>
#include <assert.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stddef.h>
#endif
#include "xframe_diff_hw.h"

/**************************** Type Definitions ******************************/
#ifdef __linux__
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
#else
typedef struct {
    u16 DeviceId;
    u64 Ctrl_BaseAddress;
} XFrame_diff_Config;
#endif

typedef struct {
    u64 Ctrl_BaseAddress;
    u32 IsReady;
} XFrame_diff;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XFrame_diff_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XFrame_diff_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XFrame_diff_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XFrame_diff_ReadReg(BaseAddress, RegOffset) \
    *(volatile u32*)((BaseAddress) + (RegOffset))

#define Xil_AssertVoid(expr)    assert(expr)
#define Xil_AssertNonvoid(expr) assert(expr)

#define XST_SUCCESS             0
#define XST_DEVICE_NOT_FOUND    2
#define XST_OPEN_DEVICE_FAILED  3
#define XIL_COMPONENT_IS_READY  1
#endif

/************************** Function Prototypes *****************************/
#ifndef __linux__
int XFrame_diff_Initialize(XFrame_diff *InstancePtr, u16 DeviceId);
XFrame_diff_Config* XFrame_diff_LookupConfig(u16 DeviceId);
int XFrame_diff_CfgInitialize(XFrame_diff *InstancePtr, XFrame_diff_Config *ConfigPtr);
#else
int XFrame_diff_Initialize(XFrame_diff *InstancePtr, const char* InstanceName);
int XFrame_diff_Release(XFrame_diff *InstancePtr);
#endif

void XFrame_diff_Start(XFrame_diff *InstancePtr);
u32 XFrame_diff_IsDone(XFrame_diff *InstancePtr);
u32 XFrame_diff_IsIdle(XFrame_diff *InstancePtr);
u32 XFrame_diff_IsReady(XFrame_diff *InstancePtr);
void XFrame_diff_EnableAutoRestart(XFrame_diff *InstancePtr);
void XFrame_diff_DisableAutoRestart(XFrame_diff *InstancePtr);

void XFrame_diff_Set_width(XFrame_diff *InstancePtr, u32 Data);
u32 XFrame_diff_Get_width(XFrame_diff *InstancePtr);
void XFrame_diff_Set_height(XFrame_diff *InstancePtr, u32 Data);
u32 XFrame_diff_Get_height(XFrame_diff *InstancePtr);

void XFrame_diff_InterruptGlobalEnable(XFrame_diff *InstancePtr);
void XFrame_diff_InterruptGlobalDisable(XFrame_diff *InstancePtr);
void XFrame_diff_InterruptEnable(XFrame_diff *InstancePtr, u32 Mask);
void XFrame_diff_InterruptDisable(XFrame_diff *InstancePtr, u32 Mask);
void XFrame_diff_InterruptClear(XFrame_diff *InstancePtr, u32 Mask);
u32 XFrame_diff_InterruptGetEnabled(XFrame_diff *InstancePtr);
u32 XFrame_diff_InterruptGetStatus(XFrame_diff *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
