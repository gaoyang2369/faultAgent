import type { IdentityKey, QuestionTemplateGroup } from './questionTemplates'

export const robotArmQuestionTemplates: Record<IdentityKey, QuestionTemplateGroup[]> = {
  tourist: [
    {
      title: "设备状态",
      subQuestions: [
        "了解DCMA的运行状态",
        "机械臂的运行情况怎么样？",
        "设备现在运行正常吗？",
        "设备的运行速度在正常范围内吗？",
      ]
    },
    {
      title: "故障诊断",
      subQuestions: [
        "对机械臂进行故障诊断",
        "分析机械臂的故障原因",
        "这个故障是什么类型的？严重吗？",
        "收到故障码后应该怎么做？",
      ]
    },
    {
      title: "维护与安全",
      subQuestions: [
        "根据现在的情况，设备需要做哪些维护？",
        "设备哪里最容易出故障？",
        "当前设备会有安全隐患吗？"
      ]
    },
    {
      title: "性能优化",
      subQuestions: [
        "设备的运行效率怎么样？有没有下降？",
        "性能下降可能是什么原因？",
        "如何优化负载以降低能耗？",
        "环境温度、湿度对设备运行有影响吗？"
      ]
    }
  ],
  admin: [
    {
      title: "故障诊断",
      subQuestions: [
        "对机械臂系统进行故障诊断",
        "诊断机械臂在2024-11-13 16:12到16:15期间的故障情况",
        "分析J3轴最近的故障，找出最重要的传感器特征",
        "基于SHAP分析，识别导致故障的关键传感器通道",
      ]
    },
    {
      title: "数据分析",
      subQuestions: [
        "对机械臂系统的J3轴的电流数据可视化",
        "了解DCMA的情况",
        "查询环境温度超过30℃的时间段，分析该时间段内设备反馈电流的变化",
        "对比正常工况和故障工况下反馈电流的分布差异",
      ]
    },
    {
      title: "故障代码",
      subQuestions: [
        "查询故障代码F01002的含义和触发原因",
        "根据故障手册描述，提取关键故障特征",
        "查询特定故障的处理步骤和安全注意事项",
        "对比多个故障代码的共同点和差异"
      ]
    },
    {
      title: "维护管理",
      subQuestions: [
        "总结一下最近一周设备的工作状态",
        "分析维护成本与故障损失，优化维护周期",
        "建立设备健康管理体系，实现预测性维护",
      ]
    }
  ]
}
