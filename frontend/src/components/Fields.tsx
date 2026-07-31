/** Small form primitives, so every sheet looks and behaves the same. */

interface FieldProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
  wide?: boolean;
}

export function Field({ label, hint, children, wide }: FieldProps) {
  return (
    <label className={`field${wide ? " field-wide" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function Row({ children }: { children: React.ReactNode }) {
  return <div className="field-row">{children}</div>;
}

interface TextProps {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  maxLength?: number;
  required?: boolean;
}

export function Text({ value, onChange, ...rest }: TextProps) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      spellCheck={false}
      {...rest}
    />
  );
}

export function TextArea({
  value,
  onChange,
  rows = 3,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function Select<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as T)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function Money({
  amount,
  currency,
  onAmount,
  onCurrency,
}: {
  amount: string;
  currency: string;
  onAmount: (v: string) => void;
  onCurrency: (v: string) => void;
}) {
  return (
    <div className="money">
      <input
        type="number"
        min="0"
        step="0.01"
        value={amount}
        placeholder="0.00"
        onChange={(e) => onAmount(e.target.value)}
      />
      <input
        value={currency}
        placeholder="USD"
        maxLength={3}
        onChange={(e) => onCurrency(e.target.value.toUpperCase())}
      />
    </div>
  );
}
