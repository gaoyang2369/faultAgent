export interface QuestionTemplateGroup {
  title: string;
  subQuestions: string[];
}

export type IdentityKey = 'tourist' | 'admin';

export const questionTemplates: Record<IdentityKey, QuestionTemplateGroup[]> = {
  tourist: [
    {
      title: "运行概览",
      subQuestions: [
        "了解DCMA的运行状态",
        "当前关键设备是否运行正常？",
        "最近有哪些主要告警趋势？",
        "当前负载和温度是否在正常范围内？",
      ]
    },
    {
      title: "异常排查",
      subQuestions: [
        "分析当前异常告警的可能原因",
        "这个故障是什么类型的？严重吗？",
        "收到故障码后应该怎么做？",
        "哪些指标最值得优先检查？",
      ]
    },
    {
      title: "维护与安全",
      subQuestions: [
        "根据现在的情况，设备需要做哪些维护？",
        "近期哪些设备最容易出故障？",
        "当前系统会有安全隐患吗？"
      ]
    },
    {
      title: "知识查询",
      subQuestions: [
        "查询故障代码F01002的含义和处理步骤",
        "根据故障手册，提取关键排查建议",
        "环境温度、湿度对设备运行有影响吗？",
        "如何优化负载以降低能耗？"
      ]
    }
  ],
  admin: [
    {
      title: "DCMA 报告",
      subQuestions: [
        "生成 DCMA 当前运行状态报告",
        "汇总今天的关键运行指标和异常情况",
        "对比最近一周的 DCMA 运行趋势",
        "列出需要重点跟踪的告警项",
      ]
    },
    {
      title: "数据分析",
      subQuestions: [
        "了解DCMA的情况",
        "查询环境温度超过30℃的时间段，分析该时间段内设备状态变化",
        "对比不同设备或工位的运行指标差异",
        "分析最近异常频次最高的设备",
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
        "总结最近一周设备的工作状态",
        "分析维护成本与故障损失，优化维护周期",
        "建立设备健康管理体系，实现预测性维护",
        "给出值班人员的优先处理建议",
      ]
    }
  ]
};
