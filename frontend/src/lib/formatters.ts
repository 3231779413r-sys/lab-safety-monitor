export const CANONICAL_SAFETY_LABELS: Record<string, string> = {
  hardhat: "未佩戴安全帽",
  mask: "未佩戴口罩",
  safety_vest: "未穿戴安全背心",
  work_clothes: "未穿工作服",
  safety_shoes: "未穿戴防护鞋",
  gloves: "未佩戴防护手套",
  goggles: "未佩戴护目镜",
  respirator: "未佩戴防毒口罩",
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

const LEGACY_SAFETY_LABELS: Record<string, string> = {
  no_goggles: CANONICAL_SAFETY_LABELS.goggles,
  no_mask: CANONICAL_SAFETY_LABELS.mask,
  no_lab_coat: "未穿实验服",
  lab_coat: "实验服",
  no_gloves: CANONICAL_SAFETY_LABELS.gloves,
  no_head_mask: "未戴头套",
  head_mask: "头套",
  no_safety_vest: CANONICAL_SAFETY_LABELS.safety_vest,
  vest: CANONICAL_SAFETY_LABELS.safety_vest,
  no_vest: CANONICAL_SAFETY_LABELS.safety_vest,
  no_hardhat: CANONICAL_SAFETY_LABELS.hardhat,
  hard_hat: CANONICAL_SAFETY_LABELS.hardhat,
  no_hard_hat: CANONICAL_SAFETY_LABELS.hardhat,
  no_safety_shoes: CANONICAL_SAFETY_LABELS.safety_shoes,
  protective_shoes: CANONICAL_SAFETY_LABELS.safety_shoes,
  no_protective_shoes: CANONICAL_SAFETY_LABELS.safety_shoes,
  no_work_clothes: CANONICAL_SAFETY_LABELS.work_clothes,
  no_respirator: CANONICAL_SAFETY_LABELS.respirator,
  gas_mask: CANONICAL_SAFETY_LABELS.respirator,
  anti_toxic_mask: CANONICAL_SAFETY_LABELS.respirator,
  no_gas_mask: CANONICAL_SAFETY_LABELS.respirator,
};

export function formatSafetyLabel(value: string): string {
  if (!value) return value;
  const withoutPrefix = value.trim().replace(/^action:/i, "");
  const normalized = withoutPrefix.toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized.startsWith("no_")) {
    const canonical = normalized.slice(3);
    if (CANONICAL_SAFETY_LABELS[canonical]) return CANONICAL_SAFETY_LABELS[canonical];
  }
  if (CANONICAL_SAFETY_LABELS[normalized]) return CANONICAL_SAFETY_LABELS[normalized];
  if (LEGACY_SAFETY_LABELS[normalized]) return LEGACY_SAFETY_LABELS[normalized];
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
