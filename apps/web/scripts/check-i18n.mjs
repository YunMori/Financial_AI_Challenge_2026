// 빌드 전 키 누락 검사 (planner §11.4).
// 다국어에서 키 누락은 **반드시** 발생한다. 화면에 키 이름이 그대로 뜨는 것을
// 배포 후에 발견하지 않도록 여기서 막는다.
import { readFileSync, readdirSync } from "node:fs";

const dir = new URL("../messages/", import.meta.url);
const load = (f) => JSON.parse(readFileSync(new URL(f, dir), "utf8"));

const flatten = (obj, prefix = "") =>
  Object.entries(obj).flatMap(([k, v]) =>
    typeof v === "object" && v !== null ? flatten(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  );

const base = "ko.json";
const baseKeys = new Set(flatten(load(base)));
let failed = false;

for (const file of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
  if (file === base) continue;
  const keys = new Set(flatten(load(file)));
  const missing = [...baseKeys].filter((k) => !keys.has(k));
  const extra = [...keys].filter((k) => !baseKeys.has(k));
  if (missing.length || extra.length) {
    failed = true;
    console.error(`\n✗ ${file}`);
    missing.forEach((k) => console.error(`    누락: ${k}`));
    extra.forEach((k) => console.error(`    잉여: ${k}`));
  } else {
    console.log(`✓ ${file}  (${keys.size}개 키)`);
  }
}

if (failed) {
  console.error("\n키가 맞지 않습니다. 화면에 키 이름이 그대로 노출됩니다.");
  process.exit(1);
}
console.log(`\n${baseKeys.size}개 키가 모든 언어에서 일치합니다.`);
