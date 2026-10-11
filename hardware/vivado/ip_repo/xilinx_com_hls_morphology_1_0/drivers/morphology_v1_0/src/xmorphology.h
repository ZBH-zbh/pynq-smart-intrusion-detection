// ==============================================================
// Vitis HLS - High-Level Synthesis from C, C++ and OpenCL v2022.1 (64-bit)
// Tool Version Limit: 2022.04
// Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
// ==============================================================
#ifndef XMORPHOLOGY_H
#define XMORPHOLOGY_H

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
#include "xmorphology_hw.h"

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
} XMorphology_Config;
#endif

typedef struct {
    u64 Ctrl_BaseAddress;
    u32 IsReady;
} XMorphology;

typedef u32 word_type;

/***************** Macros (Inline Functions) Definitions *********************/
#ifndef __linux__
#define XMorphology_WriteReg(BaseAddress, RegOffset, Data) \
    Xil_Out32((BaseAddress) + (RegOffset), (u32)(Data))
#define XMorphology_ReadReg(BaseAddress, RegOffset) \
    Xil_In32((BaseAddress) + (RegOffset))
#else
#define XMorphology_WriteReg(BaseAddress, RegOffset, Data) \
    *(volatile u32*)((BaseAddress) + (RegOffset)) = (u32)(Data)
#define XMorphology_ReadReg(BaseAddress, RegOffset) \
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
int XMorphology_Initialize(XMorphology *InstancePtr, u16 DeviceId);
XMorphology_Config* XMorphology_LookupConfig(u16 DeviceId);
int XMorphology_CfgInitialize(XMorphology *InstancePtr, XMorphology_Config *ConfigPtr);
#else
int XMorphology_Initialize(XMorphology *InstancePtr, const char* InstanceName);
int XMorphology_Release(XMorphology *InstancePtr);
#endif

void XMorphology_Start(XMorphology *InstancePtr);
u32 XMorphology_IsDone(XMorphology *InstancePtr);
u32 XMorphology_IsIdle(XMorphology *InstancePtr);
u32 XMorphology_IsReady(XMorphology *InstancePtr);
void XMorphology_EnableAutoRestart(XMorphology *InstancePtr);
void XMorphology_DisableAutoRestart(XMorphology *InstancePtr);

void XMorphology_Set_width(XMorphology *InstancePtr, u32 Data);
u32 XMorphology_Get_width(XMorphology *InstancePtr);
void XMorphology_Set_height(XMorphology *InstancePtr, u32 Data);
u32 XMorphology_Get_height(XMorphology *InstancePtr);

void XMorphology_InterruptGlobalEnable(XMorphology *InstancePtr);
void XMorphology_InterruptGlobalDisable(XMorphology *InstancePtr);
void XMorphology_InterruptEnable(XMorphology *InstancePtr, u32 Mask);
void XMorphology_InterruptDisable(XMorphology *InstancePtr, u32 Mask);
void XMorphology_InterruptClear(XMorphology *InstancePtr, u32 Mask);
u32 XMorphology_InterruptGetEnabled(XMorphology *InstancePtr);
u32 XMorphology_InterruptGetStatus(XMorphology *InstancePtr);

#ifdef __cplusplus
}
#endif

#endif
