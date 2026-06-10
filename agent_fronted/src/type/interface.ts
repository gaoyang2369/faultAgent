export interface ChartDataItem {
    id: number;
    generatorNo: string;
    temperature: number;
    recordTime: string;
    createTime: string;
}

//定义智能体message参数
export interface message {
    role: string;
    content: string;
    timestamp: string;
    isMarkdown: boolean;
    hasChart: boolean;
    chartData: ChartDataItem[];
}