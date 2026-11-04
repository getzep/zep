import { Decimal } from "decimal.js";

Decimal.set({ precision: 28, rounding: Decimal.ROUND_HALF_EVEN });

export type Product = "Cedar" | "Orchid" | "Maple" | "Willow";
export type Selection = {
  account_id: string;
  checking_account_id: string | null;
  card_id: string | null;
};
export type Savings = {
  account_id: string;
  product: Product;
  statement_balance: string;
  checking_boosted_balance: string;
  card_boosted_balance: string;
};
export type Checking = {
  account_id: string;
  product: "Everyday" | "Plus" | "Premier";
  active: boolean;
  payroll: string;
  debit_purchases: number;
};
export type Card = {
  card_id: string;
  product: "Rewards" | "Platinum" | "Travel";
  open: boolean;
  delinquent: boolean;
  purchases: string;
};
export type InterestEntry = {
  account_id: string;
  statement_month: string;
  kind: "interest" | "reversal" | "correction";
  amount: string;
};
export type Case = {
  case_id: string;
  statement_month: string;
  savings: Savings[];
  checking: Checking[];
  cards: Card[];
  interest_entries: InterestEntry[];
};
export type Credit = {
  credit_id: string;
  account_id: string;
  statement_month: string;
  amount: string;
  reason_code: string;
};
export type Report = {
  account_id: string;
  statement_month: string;
  base_rate_percent: string;
  checking_bonus_percent: string;
  card_bonus_percent: string;
  checking_boosted_balance: string;
  card_boosted_balance: string;
  expected_interest: string;
  net_interest_before_run: string;
  package_bonus_percent: string;
  package_boosted_balance: string;
  checking_account_id: string | null;
  card_id: string | null;
  correction_credit_id: string;
};
export type Database = { credits: Credit[]; reports: Report[] };
export type Quote = Omit<Report, "correction_credit_id"> & {
  permitted: true;
  annual_bonus: string;
  correction_amount: string;
};
export type Refusal = { permitted: false; reason: string };
export type Allocation = {
  permitted: true;
  quotes: Quote[];
  total_annual_bonus: string;
};

const checkingRates: Record<Product, Record<string, string>> = {
  Cedar: { Everyday: "0.30", Plus: "0.65", Premier: "1.10" },
  Orchid: { Everyday: "0.55", Plus: "0.60", Premier: "0.20" },
  Maple: { Everyday: "0.75", Plus: "0.15", Premier: "0.95" },
  Willow: { Everyday: "0.40", Plus: "0.55", Premier: "1.00" },
};
const cardRates: Record<Product, Record<string, string>> = {
  Cedar: { Rewards: "0.35", Platinum: "0.60", Travel: "0.90" },
  Orchid: { Rewards: "0.75", Platinum: "0.25", Travel: "0.40" },
  Maple: { Rewards: "0.65", Platinum: "0.80", Travel: "1.10" },
  Willow: { Rewards: "0.90", Platinum: "0.65", Travel: "1.00" },
};
const packageRates: Record<Product, Record<string, Record<string, string>>> = {
  Cedar: {
    Everyday: { Rewards: "0.50", Platinum: "0.10", Travel: "0.35" },
    Plus: { Rewards: "0.35", Platinum: "0.45", Travel: "0.10" },
    Premier: { Rewards: "0.25", Platinum: "0.50" },
  },
  Orchid: {
    Everyday: { Platinum: "0.10", Travel: "0.40" },
    Plus: { Rewards: "0.05", Platinum: "0.60", Travel: "0.30" },
    Premier: { Rewards: "0.50", Platinum: "0.35", Travel: "0.55" },
  },
  Maple: {
    Everyday: { Rewards: "0.30", Platinum: "0.50" },
    Plus: { Rewards: "0.60", Platinum: "0.05", Travel: "0.40" },
    Premier: { Rewards: "0", Platinum: "0", Travel: "0" },
  },
  Willow: {
    Everyday: { Rewards: "0.10", Platinum: "0.30", Travel: "0.20" },
    Plus: { Rewards: "0.35", Platinum: "0.15", Travel: "0.25" },
    Premier: { Rewards: "0", Travel: "0" },
  },
};
const baseRates: Record<Product, string> = {
  Cedar: "2.15",
  Orchid: "4.85",
  Maple: "3.25",
  Willow: "3.90",
};
const excluded: Record<Product, [string, string]> = {
  Cedar: ["Premier", "Travel"],
  Orchid: ["Everyday", "Rewards"],
  Maple: ["Everyday", "Travel"],
  Willow: ["Premier", "Platinum"],
};
const D = (value: Decimal.Value) => new Decimal(value);
const cents = (value: Decimal) => value.toDecimalPlaces(2, Decimal.ROUND_HALF_UP);
const money = (value: Decimal) => value.toFixed(2);
const decimalJson = (value: Decimal) => value.isZero() ? "0" : value.decimalPlaces() < 2 ? value.toFixed(2) : value.toString();

function allocationRank(total: Decimal, quotes: Quote[]): Decimal[] {
  return [total, ...quotes.flatMap((quote) => [
    D(quote.card_bonus_percent),
    D(quote.checking_bonus_percent),
  ])];
}

export function compareRank(a: readonly Decimal[], b: readonly Decimal[]): -1 | 0 | 1 {
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    if (index >= a.length) return -1;
    if (index >= b.length) return 1;
    if (a[index].lt(b[index])) return -1;
    if (a[index].gt(b[index])) return 1;
  }
  return 0;
}

class PythonRandom {
  private readonly mt = new Uint32Array(624);
  private index = 624;

  constructor(seed: number) {
    let value = BigInt(Math.abs(seed));
    const key: number[] = [];
    while (value > 0n) {
      key.push(Number(value & 0xffffffffn));
      value >>= 32n;
    }
    if (key.length === 0) key.push(0);
    this.mt[0] = 19650218;
    for (let i = 1; i < 624; i++) {
      this.mt[i] = (Math.imul(1812433253, this.mt[i - 1] ^ (this.mt[i - 1] >>> 30)) + i) >>> 0;
    }
    let i = 1;
    let j = 0;
    for (let k = Math.max(624, key.length); k > 0; k--) {
      const previous = this.mt[i - 1] ^ (this.mt[i - 1] >>> 30);
      this.mt[i] = (this.mt[i] ^ Math.imul(previous, 1664525)) + key[j] + j;
      this.mt[i] >>>= 0;
      if (++i >= 624) {
        this.mt[0] = this.mt[623];
        i = 1;
      }
      if (++j >= key.length) j = 0;
    }
    for (let k = 623; k > 0; k--) {
      const previous = this.mt[i - 1] ^ (this.mt[i - 1] >>> 30);
      this.mt[i] = (this.mt[i] ^ Math.imul(previous, 1566083941)) - i;
      this.mt[i] >>>= 0;
      if (++i >= 624) {
        this.mt[0] = this.mt[623];
        i = 1;
      }
    }
    this.mt[0] = 0x80000000;
  }

  private uint32(): number {
    if (this.index >= 624) {
      for (let i = 0; i < 624; i++) {
        const y = (this.mt[i] & 0x80000000) | (this.mt[(i + 1) % 624] & 0x7fffffff);
        this.mt[i] = (this.mt[(i + 397) % 624] ^ (y >>> 1) ^ ((y & 1) ? 0x9908b0df : 0)) >>> 0;
      }
      this.index = 0;
    }
    let y = this.mt[this.index++];
    y ^= y >>> 11;
    y ^= (y << 7) & 0x9d2c5680;
    y ^= (y << 15) & 0xefc60000;
    y ^= y >>> 18;
    return y >>> 0;
  }

  private getrandbits(bits: number): number {
    return this.uint32() >>> (32 - bits);
  }

  randbelow(limit: number): number {
    const bits = Math.floor(Math.log2(limit)) + 1;
    let result = this.getrandbits(bits);
    while (result >= limit) result = this.getrandbits(bits);
    return result;
  }

  randrange(start: number, stop: number): number {
    return start + this.randbelow(stop - start);
  }

  shuffle<T>(items: T[]): void {
    for (let i = items.length - 1; i > 0; i--) {
      const j = this.randbelow(i + 1);
      [items[i], items[j]] = [items[j], items[i]];
    }
  }
}

export function makeCase(seed: number): Case {
  const rng = new PythonRandom(seed);
  const prefix = `case-${String(seed).padStart(6, "0")}`;
  const products: Product[] = ["Cedar", "Orchid", "Maple", "Willow"];
  const savings = products.map((product, i) => {
    const balance = D(rng.randrange(700000, 2400000)).div(100)
      .plus(seed % 3 === 2 && product === "Orchid" ? 5000 : 0);
    const checking = balance.lte(5000) ? balance.toString() : "5000";
    const card = balance.lte(10000) ? balance.toString() : "10000";
    return {
      account_id: `${prefix}-s${i + 1}`,
      product,
      statement_balance: balance.toString(),
      checking_boosted_balance: checking,
      card_boosted_balance: card,
    };
  });
  const entries: InterestEntry[] = [];
  for (const account of savings) {
    entries.push(
      { account_id: account.account_id, statement_month: "2031-04", kind: "interest", amount: D(rng.randrange(500, 1000)).div(100).toString() },
      { account_id: account.account_id, statement_month: "2031-04", kind: "reversal", amount: D(rng.randrange(100, 300)).div(-100).toString() },
      { account_id: account.account_id, statement_month: "2031-04", kind: "correction", amount: D(rng.randrange(50, 150)).div(100).toString() },
      { account_id: account.account_id, statement_month: "2031-03", kind: "interest", amount: D(rng.randrange(1200, 2000)).div(100).toString() },
    );
  }
  rng.shuffle(entries);
  return {
    case_id: prefix,
    statement_month: "2031-04",
    savings,
    checking: [
      { account_id: `${prefix}-c1`, product: "Everyday", active: true, payroll: "2100", debit_purchases: 14 },
      { account_id: `${prefix}-c3`, product: "Premier", active: true, payroll: "2600", debit_purchases: 16 },
    ],
    cards: [
      { card_id: `${prefix}-k1`, product: "Rewards", open: true, delinquent: false, purchases: "800" },
      { card_id: `${prefix}-k3`, product: "Travel", open: true, delinquent: false, purchases: "900" },
    ],
    interest_entries: entries,
  };
}

function quoteValues(caseData: Case, selection: Selection): Quote | Refusal {
  const account = caseData.savings.find((item) => item.account_id === selection.account_id);
  if (!account) throw new Error("The savings account is not in this case.");
  const checking = caseData.checking.find((item) => item.account_id === selection.checking_account_id);
  const card = caseData.cards.find((item) => item.card_id === selection.card_id);
  if (selection.checking_account_id !== null &&
      (!checking || !checking.active || D(checking.payroll).lt(1500) || checking.debit_purchases < 10)) {
    return { permitted: false, reason: "The checking account does not qualify for this statement." };
  }
  if (selection.card_id !== null &&
      (!card || !card.open || card.delinquent || D(card.purchases).lt(500))) {
    return { permitted: false, reason: "The card does not qualify for this statement." };
  }
  if (checking?.product === excluded[account.product][0] && card?.product === excluded[account.product][1]) {
    return { permitted: false, reason: "The relationship policy does not permit this package." };
  }
  const checkRate = checking ? checkingRates[account.product][checking.product] : "0";
  const cardRate = card ? cardRates[account.product][card.product] : "0";
  const pkgRate = checking && card ? packageRates[account.product][checking.product][card.product] : "0";
  const checkBalance = D(account.checking_boosted_balance);
  const cardBalance = D(account.card_boosted_balance);
  const packageBalance = D(account.statement_balance).lte(7500) ? account.statement_balance : "7500";
  const annual = checkBalance.mul(checkRate).plus(cardBalance.mul(cardRate))
    .plus(D(packageBalance).mul(pkgRate)).div(100);
  const hasDecimalInput = [
    account.checking_boosted_balance, checkRate,
    account.card_boosted_balance, cardRate, packageBalance, pkgRate,
  ].some((value) => value.includes("."));
  const posted = caseData.interest_entries
    .filter((item) => item.account_id === account.account_id && item.statement_month === caseData.statement_month)
    .reduce((sum, item) => sum.plus(item.amount), D(0));
  const expected = cents(D(account.statement_balance).mul(baseRates[account.product]).div(100).plus(annual).div(12));
  return {
    permitted: true,
    account_id: account.account_id,
    statement_month: caseData.statement_month,
    base_rate_percent: baseRates[account.product],
    checking_bonus_percent: checkRate,
    card_bonus_percent: cardRate,
    checking_boosted_balance: account.checking_boosted_balance,
    card_boosted_balance: account.card_boosted_balance,
    expected_interest: money(expected),
    net_interest_before_run: money(posted),
    package_bonus_percent: pkgRate,
    package_boosted_balance: packageBalance,
    checking_account_id: selection.checking_account_id,
    card_id: selection.card_id,
    annual_bonus: annual.isZero() && !hasDecimalInput
      ? "0"
      : annual.toFixed(Math.max(2, annual.decimalPlaces())),
    correction_amount: money(expected.minus(posted)),
  };
}

export function quoteInterest(caseData: Case, selection: Selection): Quote | Refusal {
  return quoteValues(caseData, selection);
}

export function comparePackages(caseData: Case, packages: Selection[]): Allocation | Refusal {
  const accounts = [...caseData.savings].sort((a, b) => a.account_id.localeCompare(b.account_id));
  const options = new Map(accounts.map((account) => [account.account_id, new Map<string, Selection>()]));
  for (const selection of packages) {
    const choices = options.get(selection.account_id);
    if (!choices) return { permitted: false, reason: `The savings account ${selection.account_id} is not in this case.` };
    const quote = quoteValues(caseData, selection);
    if (!quote.permitted) return { permitted: false, reason: `Package ${JSON.stringify(selection)} was refused: ${quote.reason}` };
    choices.set(`${selection.checking_account_id}|${selection.card_id}`, selection);
  }
  if ([...options.values()].some((items) => items.size === 0)) {
    return { permitted: false, reason: "Submit at least one package for each savings account." };
  }
  let best: Allocation | null = null;
  let bestRank: Decimal[] | null = null;
  const chosen: Selection[] = [];
  const visit = (index: number) => {
    if (index === accounts.length) {
      const checkingIds = chosen.map((item) => item.checking_account_id).filter(Boolean);
      const cardIds = chosen.map((item) => item.card_id).filter(Boolean);
      if (new Set(checkingIds).size !== checkingIds.length || new Set(cardIds).size !== cardIds.length) return;
      const quotes = chosen.map((item) => quoteValues(caseData, item)).filter((item): item is Quote => item.permitted);
      const total = quotes.reduce((sum, item) => sum.plus(item.annual_bonus), D(0));
      const candidate: Allocation = { permitted: true, quotes, total_annual_bonus: decimalJson(total) };
      const rank = allocationRank(total, quotes);
      if (!bestRank || compareRank(rank, bestRank) > 0) {
        best = candidate;
        bestRank = rank;
      }
      return;
    }
    const choices = options.get(accounts[index].account_id)!;
    for (const selection of choices.values()) {
      chosen.push(selection);
      visit(index + 1);
      chosen.pop();
    }
  };
  visit(0);
  return best ?? { permitted: false, reason: "No complete allocation avoids source reuse among these packages." };
}

export function expectedDatabase(caseData: Case): Database {
  const options: Selection[][] = [];
  const accounts = [...caseData.savings].sort((a, b) => a.account_id.localeCompare(b.account_id));
  for (const account of accounts) {
    const choices: Selection[] = [];
    const checkings = [null, ...caseData.checking.filter((item) => item.active && D(item.payroll).gte(1500) && item.debit_purchases >= 10)];
    const cards = [null, ...caseData.cards.filter((item) => item.open && !item.delinquent && D(item.purchases).gte(500))];
    for (const checking of checkings) for (const card of cards) {
      const selection = { account_id: account.account_id, checking_account_id: checking?.account_id ?? null, card_id: card?.card_id ?? null };
      if (quoteValues(caseData, selection).permitted) choices.push(selection);
    }
    options.push(choices);
  }
  let bestSelections: Selection[] = [];
  let bestRank: Decimal[] | null = null;
  const selected: Selection[] = [];
  const visit = (index: number) => {
    if (index === options.length) {
      const checks = selected.map((item) => item.checking_account_id).filter(Boolean);
      const cards = selected.map((item) => item.card_id).filter(Boolean);
      if (new Set(checks).size !== checks.length || new Set(cards).size !== cards.length) return;
      const quotes = selected.map((item) => quoteValues(caseData, item)).filter((item): item is Quote => item.permitted);
      const total = quotes.reduce((sum, quote) => sum.plus(D(quote.annual_bonus)), D(0));
      const rank = allocationRank(total, quotes);
      if (!bestRank || compareRank(rank, bestRank) > 0) {
        bestRank = rank;
        bestSelections = [...selected];
      }
      return;
    }
    for (const item of options[index]) {
      selected.push(item);
      visit(index + 1);
      selected.pop();
    }
  };
  visit(0);
  const database: Database = { credits: [], reports: [] };
  for (const selection of bestSelections) {
    const quote = quoteValues(caseData, selection);
    if (!quote.permitted || D(quote.correction_amount).lte(0)) continue;
    const creditId = `credit-${selection.account_id}-${caseData.statement_month}`;
    database.credits.push({ credit_id: creditId, account_id: selection.account_id, statement_month: caseData.statement_month, amount: quote.correction_amount, reason_code: "interest_shortfall" });
    const { permitted: _p, annual_bonus: _a, correction_amount: _c, ...report } = quote;
    database.reports.push({ ...report, correction_credit_id: creditId });
  }
  return database;
}

export function score(caseData: Case, actual: Database) {
  const normalize = (db: Database) => ({
    credits: [...db.credits].sort((a, b) => a.account_id.localeCompare(b.account_id)),
    reports: [...db.reports].sort((a, b) => a.account_id.localeCompare(b.account_id)),
  });
  const expected = normalize(expectedDatabase(caseData));
  const got = normalize(actual);
  const differences: Record<string, unknown> = {};
  for (const key of ["credits", "reports"] as const) {
    if (JSON.stringify(expected[key]) !== JSON.stringify(got[key])) differences[key] = { expected: expected[key], actual: got[key] };
  }
  return { passed: Object.keys(differences).length === 0, expected, actual: got, differences };
}

export function calculate(expression: string): string {
  if (expression.length > 500) throw new Error("The expression is too long.");
  const tokens = expression.match(/(?:\d+(?:\.\d*)?|\.\d+)|[()+\-*/]|\s+/g) ?? [];
  if (tokens.join("") !== expression) throw new Error("Only numeric addition, subtraction, multiplication and division are allowed.");
  const output: string[] = [];
  const operators: string[] = [];
  let expectValue = true;
  for (const raw of tokens) {
    const token = raw.trim();
    if (!token) continue;
    if (/^(?:\d|\.)/.test(token)) {
      if (!expectValue) throw new Error("Invalid arithmetic expression.");
      output.push(token);
      expectValue = false;
    } else if (token === "(") {
      if (!expectValue) throw new Error("Invalid arithmetic expression.");
      operators.push(token);
    } else if (token === ")") {
      while (operators.length && operators.at(-1) !== "(") output.push(operators.pop()!);
      if (operators.pop() !== "(") throw new Error("Invalid arithmetic expression.");
      expectValue = false;
    } else {
      const op = expectValue && (token === "+" || token === "-") ? `u${token}` : token;
      const precedence = (value: string) => value.startsWith("u") ? 3 : value === "*" || value === "/" ? 2 : 1;
      while (operators.length && operators.at(-1) !== "(" && precedence(operators.at(-1)!) >= precedence(op)) output.push(operators.pop()!);
      operators.push(op);
      expectValue = true;
    }
  }
  while (operators.length) {
    const op = operators.pop()!;
    if (op === "(") throw new Error("Invalid arithmetic expression.");
    output.push(op);
  }
  const stack: Decimal[] = [];
  for (const token of output) {
    if (/^(?:\d|\.)/.test(token)) stack.push(D(token));
    else if (token.startsWith("u")) {
      const value = stack.pop();
      if (!value) throw new Error("Invalid arithmetic expression.");
      stack.push(token === "u-" ? value.negated() : value);
    } else {
      const right = stack.pop(); const left = stack.pop();
      if (!left || !right) throw new Error("Invalid arithmetic expression.");
      stack.push(token === "+" ? left.plus(right) : token === "-" ? left.minus(right) : token === "*" ? left.mul(right) : left.div(right));
    }
  }
  if (stack.length !== 1 || !stack[0].isFinite()) throw new Error("The result must be finite.");
  return money(cents(stack[0]));
}
