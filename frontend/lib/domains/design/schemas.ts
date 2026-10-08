export type BomItem = {
  partNo: string;
  name: string;
  qty: number;
  unit: string;
  unitCost?: number;
  vendor?: string;
};

export type DrawingRef = {
  drawingNo: string;
  rev: string;
  title: string;
};
