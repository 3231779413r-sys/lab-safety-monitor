// PPE 标签映射
const LABEL_OVERRIDES: Record<string, string> = {
  no_goggles: "未佩戴护目镜",
  goggles: "未佩戴护目镜",
  no_mask: "未佩戴口罩",
  mask: "未佩戴口罩",
  no_lab_coat: "未穿实验服",
  lab_coat: "实验服",
  no_gloves: "未佩戴防护手套",
  gloves: "未佩戴防护手套",
  no_head_mask: "未戴头套",
  head_mask: "头套",
  no_safety_vest: "未穿戴安全背心",
  safety_vest: "未穿戴安全背心",
  no_hardhat: "未佩戴安全帽",
  hardhat: "未佩戴安全帽",
  no_safety_shoes: "未穿戴防护鞋",
  safety_shoes: "未穿戴防护鞋",
  protective_shoes: "未穿戴防护鞋",
  respirator: "未佩戴防毒口罩",
  gas_mask: "未佩戴防毒口罩",
  anti_toxic_mask: "未佩戴防毒口罩",
  no_respirator: "未佩戴防毒口罩",
  no_gas_mask: "未佩戴防毒口罩",
  drinking: "饮水",
  eating: "进食",
  fall_detected: "人员跌倒",
  missed_inspection: "未巡检",
  area_missed_inspection: "区域漏巡",
  unauthorized_intrusion: "违规闯入",
  overtime_stay: "超时驻留",
  blind_spot_stay: "盲区驻留",
  area_overcapacity: "区域超员",
  workshop_overcapacity: "车间超员",
};

export function formatSafetyLabel(value: string): string {
  if (!value) return value;
  const withoutPrefix = value.trim().replace(/^action:/i, "");
  const normalized = withoutPrefix.toLowerCase().replace(/[\s-]+/g, "_");
  if (LABEL_OVERRIDES[normalized]) return LABEL_OVERRIDES[normalized];
  return normalized.replace(/_/g, " ").split(" ").map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

// 格式化人员 ID
export function formatPersonId(personId: string | null | undefined): string {
  if (!personId) return "未知人员";
  if (/^unknown:/i.test(personId)) return "未知人员";
  if (/^(person|track)_\d+$/i.test(personId)) {
    const match = personId.match(/^(person|track)_(\d+)$/i);
    if (match) {
      const prefix = match[1].toLowerCase() === "person" ? "人员" : "轨迹";
      return `${prefix} #${match[2]}`;
    }
  }
  return personId;
}
