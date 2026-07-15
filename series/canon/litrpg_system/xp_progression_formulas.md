---
id: xp_progression_formulas
name: XP & Progression Formulas
kind: system
status: canon · working draft
home: series/canon/litrpg_system/xp_progression_formulas.md
last_updated: 2026-07-14
---

# XP & Progression Formulas — Dominion Realm

> **Purpose:** Owns class-level XP thresholds, class-rarity XP burden, per-scene XP gain, and combat XP adaptation formulas.
> **Agent readability:** This file is written so drafting, critique, systems, and spreadsheet agents can use the formulas without rediscovering ownership rules.
> **Do not duplicate:** `classes.md` owns class taxonomy and profiles. This file owns XP math. `resource_system.md` owns attributes, pools, recovery, and depletion; `mechanics.md` owns independent tier ladders.
> **Formatting rule:** Use aligned `$$ ... $$` display equations and place the alignment marker before the main relational operator. Avoid dense inline math because some renderers fail on inline notation.

---

## 0. Ownership and cross-reference rule

`classes.md` contains only a short XP ownership pointer and rarity-ladder summary:

```text
Class XP thresholds, class-rarity burden, scene XP gain, and combat XP formulas live in xp_progression_formulas.md. Do not duplicate the formulas here.
```

Rationale:

- `classes.md` owns what a class **is**: method, specialization, domain separation, class profiles, and rarity taxonomy.
- This file owns how class progression **costs** and how experience becomes XP.
- Class rarity affects XP and energy cost, not recurring bonus attribute-point cadence.
- Classes are earned through behavior, not selected.
- Class fit is never allowed to collapse a person into one-dimensional identity.


Current cross-file precedence:

```text
xp_progression_formulas.md owns XP thresholds, rarity burden, adaptive evidence, combat XP, and progression pacing math.
resource_system.md supplies final resource and recovery states but does not calculate XP.
mechanics.md supplies independent tier ladders but does not define class-rarity XP.
combat_defense.md emits raw CombatAdaptationTrace facts but does not award XP.
```

---

## 1. Current rarity ladder decision

Use this rarity ladder for XP calculations:

```text
Common → Uncommon → Rare → Epic → Fabled → Legendary → Mythic → Unique
```

Decision notes:

- **Exceptional is retired as a rarity name for XP tables.** Use **Epic** in its place unless a later author decision restores Exceptional.
- **Fabled** is the bridge tier between Epic and Legendary.
- **Legendary** is one per **1,000,000** Common-class people.
- **Mythic** is one per **100,000,000** Common-class people by current calibration. This can be changed if the cosmology needs Mythic at one per 10,000,000 instead.
- **Unique** is one per cosmic cycle. It remains symbolic until the number of Common-class lives/events in a cosmic cycle is defined.

Naming logic:

```text
Epic = extraordinary but still socially legible.
Fabled = known through stories, reports, institutions, and uncertain records.
Legendary = recorded by history and capable of reshaping institutions or ages.
Mythic = principle-scale, barely repeatable, and often not fully understood.
Unique = one-of-one across a cosmic cycle or causal impossibility.
```

---

## 2. Core doctrine

XP is not a reward token.

XP is the interface-visible residue of recoverable adaptation:

```text
biological strain
+ neural learning
+ resource-channel expansion
+ identity stabilization
+ aetheric reconfiguration
+ consequence integrated across recovery time
```

A class does not decide what a person is allowed to learn from.

A class changes how lived experience is interpreted.

Therefore:

1. **XP threshold** answers: how much integrated adaptive volume is required to cross from level `L` to `L + 1`.
2. **XP gain** answers: how much recoverable adaptation an event produced.
3. **Class rarity** is self-information derived from prevalence, not a flat multiplier table.
4. **Class coupling** is a soft nonzero kernel, not a hard yes/no projection.
5. **Medical/physical strain matters.** Progression belongs to an embodied organism, not an abstract point moving through class space.


### Narrative Pacing Targets

These targets are pacing constraints, not alternate XP formulas:

| Story point | Average level range | Notes |
|---|---:|---|
| Arrival | 1 | New arrivals are functionally level 1. |
| Early Book 1 | 2–4 | Survival, first lessons, and early class pressure. |
| Mid Book 1 | 5–7 | Competence emerges; characters remain fragile. |
| Book 1 finale | 8–12 | The cast can matter in a crisis without becoming regional powers. |
| Book 2 average | about 20 | Higher-rarity paths increasingly express progression drag. |
| Book 3 average | about 30 | Growth continues, with rarity and method fit increasingly visible. |

Rule of thumb:

```text
Average cast growth is approximately ten levels per book, subject to class rarity, scene evidence, recovery, and story structure.
```

Class acquisition timing itself belongs to `classes.md`, character dossiers, and book planning. This file begins applying a class's rarity burden only when that class is active; past levels are not recalculated.

---

## 3. Total progression state

Let the character's total state be:

$$
\begin{aligned}
x(t) &\in \mathcal{M}
\\
&=
\mathcal{B}
\times
\mathcal{N}
\times
\mathcal{R}
\times
\mathcal{I}
\times
\mathcal{A}
\times
\mathcal{W}
\end{aligned}
$$

Where:

| Component | Meaning |
|---|---|
| `B` | Body: tissue, muscle, tendon, bone, organs, injury, fatigue, repair |
| `N` | Nervous system: attention, motor learning, pain gating, fear response, autonomic tone |
| `R` | Resources: mana, stamina, reserve, metabolic/aetheric capacity |
| `I` | Identity: will, vows, self-continuity, class coherence, Conviction/Mystery |
| `A` | Aetheric/interface structure: channels, domains, interface load, spell architecture |
| `W` | World context: threat, opposition, environment, social consequence, causal stakes |

A level is a stable shell of this whole organismic state.

$$
\begin{aligned}
\Sigma_L \subset \mathcal{M}
\end{aligned}
$$

Leveling is the crossing:

$$
\begin{aligned}
\Sigma_L &\longrightarrow \Sigma_{L+1}
\end{aligned}
$$

---

## 4. Threshold XP model

### 4.1 Baseline adaptive volume

The baseline adaptive volume is:

$$
\begin{aligned}
\mathcal{V}_0(L)&=L^{D_0}
\end{aligned}
$$

The value of `D0` is not chosen freely. It is solved from the Common-class pacing anchors:

$$
\begin{aligned}
XP_{Common}(1)&=100
\end{aligned}
$$

$$
\begin{aligned}
XP_{Common}(20)&=5216
\end{aligned}
$$

This gives:

$$
\begin{aligned}
D_0 &\approx 2.5177067041
\end{aligned}
$$

Interpretation:

`D0` is the effective organismic growth dimension implied by the chosen Common curve. It represents the scaling of body, nervous system, resource channels, identity stability, and aetheric tolerance before class-rarity information is added.

---

### 4.2 Rarity as self-information

Class-rarity information is derived from prevalence.

$$
\begin{aligned}
\mathscr{I}_{\mathcal{C}}
&=
\ln
\left(
\frac{p_{Common}}{p_{\mathcal{C}}}
\right)
\end{aligned}
$$

Current prevalence assumptions:

| Rarity | Prevalence relative to Common | Self-information | Notes |
|---|---:|---:|---|
| Common | 1 | 0.000 | Reference basin. |
| Uncommon | 1 / 10 | 2.303 | One per ten Common-class people. |
| Rare | 1 / 100 | 4.605 | One per hundred Common-class people. |
| Epic | 1 / 1,000 | 6.908 | Renamed replacement for Exceptional; one per thousand Common-class people. |
| Fabled | 1 / 100,000 | 11.513 | Bridge tier between Epic and Legendary; one per one hundred thousand Common-class people. |
| Legendary | 1 / 1,000,000 | 13.816 | One per one million Common-class people. |
| Mythic | 1 / 100,000,000 | 18.421 | One per one hundred million Common-class people. |

Unique is one per cosmic cycle:

$$
\begin{aligned}
\mathscr{I}_{Unique}
&=
\ln N_{cycle}
\end{aligned}
$$

Where:

```text
N_cycle = number of Common-class lives, attempts, or causally relevant class-bearing events in one cosmic cycle.
```

Agent rule:

Do not calculate a numeric Unique XP column until `N_cycle` is defined. If a temporary number is required for testing only, mark it as non-canon.

---

### 4.3 Embodied rarity fraction

Rarity burden should not fully express at level 1. A rare class starts as a seed and becomes expensive as the organism embodies more of its information content.

Baseline accumulated adaptation:

$$
\begin{aligned}
\mathcal{A}_0(L)
&=
\ln
\left(
\frac{\mathcal{V}_0(L)}{\mathcal{V}_0(1)}
\right)
\end{aligned}
$$

Embodied rarity fraction:

$$
\begin{aligned}
\eta_{\mathcal{C}}(L)
&=
\begin{cases}
0,
& \mathscr I_{\mathcal C}=0,
\\
1-
\exp
\left(
-
\dfrac{\mathcal A_0(L)}{1+\mathscr I_{\mathcal C}}
\right),
& \mathscr I_{\mathcal C}>0.
\end{cases}
\end{aligned}
$$

The `1 +` in the denominator is a regularizer. It prevents singular behavior near Common and makes low-information classes unfold quickly.

---

### 4.4 Class-adjusted adaptive volume

Class-adjusted adaptive volume:

$$
\begin{aligned}
\mathcal{V}_{\mathcal{C}}(L)
&=
\mathcal{V}_0(L)
\exp
\left(
\beta
\mathscr{I}_{\mathcal{C}}
\eta_{\mathcal{C}}(L)
\right)
\end{aligned}
$$

`beta` is solved from the design anchor:

$$
\begin{aligned}
XP_{Legendary}(20)
&=
1.38
\cdot
XP_{Common}(20)
\end{aligned}
$$

This gives:

$$
\begin{aligned}
\beta &\approx 0.0756920571
\end{aligned}
$$

Agent rule:

- Do not hand-edit `beta` directly.
- If pacing changes, change the anchor and recompute `beta`.

---

### 4.5 XP to next level

XP required to advance from level `L` to `L + 1`:

$$
\begin{aligned}
XP_{\mathcal{C}}(L)
&=
100
\cdot
\frac
{
\mathcal{V}_{\mathcal{C}}(L+1)
-
\mathcal{V}_{\mathcal{C}}(L)
}
{
\mathcal{V}_{\mathcal{C}}(2)
-
\mathcal{V}_{\mathcal{C}}(1)
}
\end{aligned}
$$

This normalization forces:

$$
\begin{aligned}
XP_{\mathcal{C}}(1)&=100
\end{aligned}
$$

for every numeric rarity tier.

Design rounding:

```text
Design tables: nearest tenth.
Interface display: nearest whole number.
```

---

## 5. Current level 1–20 threshold table

Rounded to nearest tenth for design.

| Level | Common | Uncommon | Rare | Epic | Fabled | Legendary | Mythic |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| 2 | 215.1 | 221.2 | 225.5 | 227.9 | 230.5 | 231.2 | 232.2 |
| 3 | 357.6 | 372.7 | 384.8 | 392.0 | 399.9 | 402.2 | 405.5 |
| 4 | 523.0 | 549.6 | 572.7 | 587.0 | 603.0 | 608.0 | 614.8 |
| 5 | 708.9 | 749.0 | 785.9 | 809.5 | 836.6 | 845.0 | 856.7 |
| 6 | 913.2 | 968.7 | 1,022.2 | 1,057.3 | 1,098.2 | 1,111.0 | 1,128.9 |
| 7 | 1,134.4 | 1,207.1 | 1,279.8 | 1,328.3 | 1,385.8 | 1,403.9 | 1,429.4 |
| 8 | 1,371.6 | 1,462.9 | 1,557.2 | 1,621.2 | 1,697.9 | 1,722.4 | 1,756.8 |
| 9 | 1,623.7 | 1,735.1 | 1,853.3 | 1,934.7 | 2,033.3 | 2,065.0 | 2,109.7 |
| 10 | 1,889.9 | 2,022.9 | 2,167.1 | 2,267.7 | 2,390.9 | 2,430.7 | 2,487.1 |
| 11 | 2,169.6 | 2,325.4 | 2,497.8 | 2,619.5 | 2,769.8 | 2,818.6 | 2,888.1 |
| 12 | 2,462.2 | 2,642.1 | 2,844.6 | 2,989.2 | 3,169.2 | 3,227.9 | 3,311.7 |
| 13 | 2,767.2 | 2,972.3 | 3,207.0 | 3,376.2 | 3,588.3 | 3,657.9 | 3,757.4 |
| 14 | 3,084.1 | 3,315.7 | 3,584.4 | 3,779.8 | 4,026.5 | 4,107.8 | 4,224.4 |
| 15 | 3,412.6 | 3,671.7 | 3,976.3 | 4,199.6 | 4,483.3 | 4,577.1 | 4,712.1 |
| 16 | 3,752.2 | 4,039.9 | 4,382.2 | 4,634.9 | 4,958.1 | 5,065.4 | 5,220.0 |
| 17 | 4,102.6 | 4,420.0 | 4,801.7 | 5,085.5 | 5,450.4 | 5,572.0 | 5,747.7 |
| 18 | 4,463.6 | 4,811.7 | 5,234.4 | 5,550.9 | 5,959.8 | 6,096.6 | 6,294.6 |
| 19 | 4,834.8 | 5,214.5 | 5,680.0 | 6,030.6 | 6,486.0 | 6,638.8 | 6,860.3 |
| 20 | 5,216.0 | 5,628.3 | 6,138.1 | 6,524.4 | 7,028.4 | 7,198.1 | 7,444.4 |

Ratio to Common:

| Level | Common | Uncommon | Rare | Epic | Fabled | Legendary | Mythic |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| 5 | 1.000 | 1.057 | 1.109 | 1.142 | 1.180 | 1.192 | 1.209 |
| 10 | 1.000 | 1.070 | 1.147 | 1.200 | 1.265 | 1.286 | 1.316 |
| 20 | 1.000 | 1.079 | 1.177 | 1.251 | 1.347 | 1.380 | 1.427 |
| 50 | 1.000 | 1.086 | 1.206 | 1.307 | 1.449 | 1.500 | 1.575 |
| 100 | 1.000 | 1.088 | 1.222 | 1.341 | 1.519 | 1.585 | 1.686 |

---

## 6. Scene XP as adaptive evidence

### 6.1 Why scene XP is not a flat award

Scene XP should not be assigned as kill-count or quest-count.

Scene XP is the interface projection of **integrated adaptive evidence** produced by a scene.

Use a dimensionless adaptive evidence variable:

$$
\begin{aligned}
\mathcal{E}_{\mathcal{C}}(e,L)
&\geq
0
\end{aligned}
$$

Then scene XP is the threshold fraction implied by that evidence:

$$
\begin{aligned}
\Delta XP_{\mathcal{C}}(e,L)
&=
XP_{\mathcal{C}}(L)
\left(
1-
\exp
\left[-\mathcal{E}_{\mathcal{C}}(e,L)\right]
\right)
\end{aligned}
$$

This gives a natural saturation curve:

| Adaptive evidence | Threshold fraction |
|---:|---:|
| 0.04 | 0.0392 |
| 0.10 | 0.0952 |
| 0.25 | 0.2212 |
| 0.45 | 0.3624 |
| 0.75 | 0.5276 |
| 1.25 | 0.7135 |

Agent rule:

A huge scene can give a large fraction of a level, but the exponential form prevents ordinary additive stacking from making a single unbounded scene trivially jump many levels unless the scene contains repeated threshold events or an explicit breakthrough.

---

### 6.2 Continuous-time adaptive evidence

For a scene `e` over time interval `[t0, t1]`:

$$
\begin{aligned}
\mathcal{E}_{\mathcal{C}}(e,L)
&=
\int_{t_0}^{t_1}
\Psi_{\mathcal{C}}
\left(
X_t,
u_t,L
\right)
dt
+
\sum_{k \in J_e}
\mathcal{J}_{\mathcal{C},k}
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| `X_t` | Full progression state at time `t` |
| `u_t` | Control/action mode being expressed at time `t` |
| `Psi_C` | instantaneous recoverable adaptive evidence rate |
| `J_e` | discrete jump events: breakthrough, injury, vow event, interface overload, insight, class recognition |
| `mathcal J_C,k` | adaptive evidence added by jump event `k` |

The evidence rate:

$$
\begin{aligned}
\Psi_{\mathcal{C}}
&=
\gamma_0
\cdot
\Lambda(X_t,
u_t)
\cdot
H(Z_t)
\cdot
\Theta_t
\cdot
\Xi_t
\cdot
\left[
\rho_{\mathcal{C}}(L)
+
\left(
1-
\rho_{\mathcal{C}}(L)
\right)
K_{\mathcal{C}}(X_t,\xi_t)
\right]
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| `gamma_0` | scene-scale calibration constant, solved from a reference-scene pacing target |
| `Lambda` | total organismic adaptive load |
| `H` | hormetic adaptation window |
| `Theta` | recovery/integration quality |
| `Xi` | consequence/information weight |
| `rho_C` | general organism contribution floor |
| `K_C` | nonzero class-method heat-kernel coupling |
| `xi_t` | normalized lived-action direction |

Agent rule:

The only scene-scale calibration constant should be `gamma_0`. Derive it from a stated reference scene, such as: "one serious class-relevant combat at level 10 should provide about 20% of the level threshold." Do not add separate arbitrary multipliers for combat, training, travel, and social scenes unless a subsystem file defines them.

---

## 7. Stochastic / analytic scene approximation

Scene adaptation can be modeled as an SDE when uncertainty, injury, and breakthrough are present.

### 7.1 State SDE

Let the organismic progression state evolve as:

$$
\begin{aligned}
dX_t
&=
b(X_t,
u_t)dt
+
\sigma(X_t,
u_t)dW_t
+
\int_Z
j(X_{t^-},z)
N(dt,dz)
\end{aligned}
$$

Where:

| Term | Meaning |
|---|---|
| `b` | expected adaptation drift from action and recovery |
| `sigma dW_t` | stochastic variation: stress response, pain, timing, uncertainty, failure/success variance |
| `N(dt,dz)` | jump process for injury, breakthrough, insight, class recognition, trauma, interface overload |
| `j` | state displacement from jump event |

### 7.2 Adaptive evidence process

Track cumulative adaptive evidence:

$$
\begin{aligned}
dY^{\mathcal{C}}_t
&=
\Psi_{\mathcal{C}}(X_t,
u_t,L)dt
+
\sigma_Y(X_t,
u_t)dB_t
+
\int_Z
\chi_{\mathcal{C}}(X_{t^-},z)
N(dt,dz)
\end{aligned}
$$

Expected scene XP:

$$
\begin{aligned}
\mathbb{E}
\left[
\Delta XP_{\mathcal{C}}
\right]
&=
XP_{\mathcal{C}}(L)
\left(
1-
\mathbb{E}
\left[
\exp(-Y^{\mathcal{C}}_{t_1})
\right]
\right)
\end{aligned}
$$

Cumulative adaptive evidence is nonnegative. Any numerical SDE integrator must enforce:

$$
\begin{aligned}
Y_t^{\mathcal C}
&\geq
0
\end{aligned}
$$

A simple discrete implementation may project after each step:

$$
\begin{aligned}
Y_{n+1}^{\mathcal C}
&=
\max
\left(
0,
\widetilde Y_{n+1}^{\mathcal C}
\right)
\end{aligned}
$$

where $\widetilde Y_{n+1}^{\mathcal C}$ is the unconstrained stochastic update. The small-variance approximation below is valid only when probability mass below zero is negligible or the nonnegative projection has been incorporated.

If variance is small:

$$
\begin{aligned}
\mathbb{E}
\left[
\Delta XP_{\mathcal{C}}
\right]
&\approx
XP_{\mathcal{C}}(L)
\left(
1-
\exp
\left[-\mathbb{E}Y^{\mathcal{C}}_{t_1}
+
\frac12
\operatorname{Var}(Y^{\mathcal{C}}_{t_1})
\right]
\right)
\end{aligned}
$$

Agent rule:

Use the deterministic integral for ordinary drafting and tables. Use the SDE form when modeling uncertainty, repeated exposures, breakthrough probabilities, or trauma/backlash risk.

### 7.3 Fokker-Planck approximation

If agents model a distribution over possible scene states, use:

$$
\begin{aligned}
\partial_t p(x,t)
&=
-
\nabla \cdot
\left(
b(x,
u_t)p(x,t)
\right)
+
\frac12
\nabla\nabla:
\left(
D(x,
u_t)p(x,t)
\right)
+
\mathcal{J}^*p(x,t)
\end{aligned}
$$

Where:

$$
\begin{aligned}
D(x,
u_t)&=\sigma(x,
u_t)\sigma(x,
u_t)^T
\end{aligned}
$$

Expected adaptive evidence rate:

$$
\begin{aligned}
\frac{d}{dt}
\mathbb{E}Y^{\mathcal{C}}_t
&=
\int_{\mathcal{M}}
\Psi_{\mathcal{C}}(x,
u_t,L)
p(x,t)dx
+
\int_{\mathcal{M}}
\int_Z
\chi_{\mathcal{C}}(x,z)
\lambda(x,z)
p(x,t)dzdx
\end{aligned}
$$

This is the analytic bridge from scene simulation to expected XP.

---

## 8. Organismic adaptive load

Use:

$$
\begin{aligned}
\Lambda(t)
&=
\sqrt
{
\dot{x}(t)^T
G_{org}(x(t))
\dot{x}(t)
}
\end{aligned}
$$

The organismic metric $G_{org}(x)$ must be symmetric positive semidefinite so $\Lambda(t)$ remains real and nonnegative.

with:

$$
\begin{aligned}
G_{org}
&=
G_{bio}
\oplus
G_{neuro}
\oplus
G_{res}
\oplus
G_{id}
\oplus
G_{aether}
\end{aligned}
$$

Meaning:

| Tensor block | Captures |
|---|---|
| `G_bio` | tissue load, muscle/tendon strain, injury, fatigue, repair pressure |
| `G_neuro` | perception, attention, fear, pain gating, motor learning, autonomic strain |
| `G_res` | mana, stamina, reserve depletion and channel expansion |
| `G_id` | vow pressure, self-continuity, class coherence, Conviction/Mystery |
| `G_aether` | domain/interface load, spell structure, channel conductivity, backlash tolerance |

For discrete scenes, approximate the integral over significant beats:

$$
\begin{aligned}
\int_e \Lambda(t)dt
\\
&\approx
\sum_b
\sqrt
{
z_{bio,b}^2
+
z_{neuro,b}^2
+
z_{res,b}^2
+
z_{id,b}^2
+
z_{aether,b}^2
}
\Delta t_b
\end{aligned}
$$

`z` values are ratios against current capacity, not arbitrary scores.

---

## 9. Strain ratios

Biological strain:

$$
\begin{aligned}
z_{bio}
&=
\frac
{
\text{mechanical load}
+
\text{injury load}
+
\text{metabolic load}
}
{
\text{current biological capacity}
}
\end{aligned}
$$

Neural strain:

$$
\begin{aligned}
z_{neuro}
&=
\frac
{
\text{attention load}
+
\text{pain load}
+
\text{fear load}
+
\text{novelty load}
+
\text{motor-control load}
}
{
\text{current neural capacity}
}
\end{aligned}
$$

Resource-depletion notation uses the nonnegative loss part:

$$
\begin{aligned}
\Delta R_-
&=
\max
\bigl(
0,-\Delta R
\bigr)
\end{aligned}
$$

Resource strain:

$$
\begin{aligned}
z_{res}
&=
\frac{\Delta Mana_-}{Mana_{max}}
+
\frac{\Delta Stamina_-}{Stamina_{max}}
+
\frac{\Delta Reserve_-}{Reserve_{max}}
\end{aligned}
$$

Identity strain:

$$
\begin{aligned}
z_{id}
&=
\frac
{
\text{vow pressure}
+
\text{moral cost}
+
\text{self-continuity stress}
+
\text{class-defining choice pressure}
}
{
\text{identity stability}
}
\end{aligned}
$$

Aetheric strain:

$$
\begin{aligned}
z_{aether}
&=
\frac
{
\text{interface load}
+
\text{domain resistance}
+
\text{spell-structure load}
+
\text{backlash pressure}
}
{
\text{aetheric tolerance}
}
\end{aligned}
$$

Agent rule:

If exact values are unavailable, estimate ratios from already-canon quantities: HP loss, Stamina loss, Mana loss, Reserve use, injury severity, number of simultaneous threats, novelty, and whether the scene forced a class-defining choice.

---

## 10. Hormetic adaptation window

Adaptation is strongest inside a recoverable stress window. Too little stress teaches little. Too much stress becomes damage, shock, trauma, or inefficiency.

For each channel `j`:

$$
\begin{aligned}
\widetilde z_j
&=
\max
\bigl(
 z_j,\varepsilon_H
\bigr),
\qquad
H_j(z_j)
\\
&=
h_j
+
(1-h_j)
\exp
\left(
-
\frac
{
\left(
\ln \widetilde z_j
-
\ln z_j^*
\right)^2
}
{
2\sigma_j^2
}
\right)
\end{aligned}
$$

Aggregate window:

$$
\begin{aligned}
H(t)
&=
\prod_j
H_j(z_j(t))^{w_j}
\end{aligned}
$$

Rules:

- `h_j` is the nonzero adaptation floor.
- $z_j^*>0$ is the optimal recoverable strain for that channel.
- $\varepsilon_H>0$ prevents the logarithm from becoming undefined at zero strain.
- `sigma_j` is tolerance width.
- `w_j` is that channel's contribution weight.
- No channel should silently erase all XP unless the event is biologically unrecoverable or metaphysically blocked.

---

## 11. Recovery and integration quality

The body does not fully adapt during the event. It adapts during recovery and integration.

Use:

$$
\begin{aligned}
\Theta_e
&=
1
-
\exp
\left(
-
\int_{t_1}^{t_2}
r_{rec}(\tau)d\tau
\right)
\end{aligned}
$$

Where `r_rec` includes:

```text
sleep
food
healing
safety
downtime
reflection
instruction
repetition
emotional integration
mana/stamina/reserve recovery
```

Agent rule:

Combat XP can be provisionally earned during combat, but full class integration should usually finalize after survival and recovery.

---

## 12. Consequence / information weight

Events matter more when they change the character's state, world-model, risk profile, social standing, or causal situation.

Use information gain:

$$
\begin{aligned}
\Xi_e
&=
1
+
\omega_{info}
D_{\mathrm{KL}}
\left(
P_{after}
\,\|\,
P_{before}
\right)
\end{aligned}
$$

For numerical work, smooth zero-probability bins or restrict both distributions to a shared support before evaluating $D_{\mathrm{KL}}$; otherwise a newly assigned positive probability over a zero baseline can make the divergence infinite.

Meaning:

- Routine practice has low information gain.
- A first fight with a new monster type has higher information gain.
- A near-death tactical discovery has high information gain.
- A vow-breaking or identity-defining moment may have very high information gain.
- Repetition still helps, but diminishing information gain prevents grind from becoming the best story logic.

---

## 13. Class-method coupling

Normalize the direction of lived action:

$$
\begin{aligned}
\xi(t)
&=
\frac{\dot{x}(t)}{\|\dot{x}(t)\|},
\qquad
\|\dot{x}(t)\|>\varepsilon_x
\end{aligned}
$$

If $\|\dot x(t)\|\leq\varepsilon_x$, no meaningful lived-action direction exists. Use only the general organism contribution floor for that interval rather than projecting a zero vector into projective method space.

Each class has a method distribution:

$$
\begin{aligned}
\nu_{\mathcal{C}}
\end{aligned}
$$

This is not a single vector. It is a distribution over ways the class can express.

Examples:

```text
Warrior: confrontation, pressure, bodily commitment, decisive action
Mage: supernatural structure, spell logic, mana manipulation, conceptual control
Scout: route-finding, contact, movement, observation, entry into unfamiliar spaces
Warden: boundary maintenance, protection, transferred-cost perception
Psion: attention, salience, thought-pressure, mind/world interpretation
Realmwalker: boundary reading, passage, topology, world-distance, return-path recognition
Worldbreaker: pressure, severance, impossible opposition, scale-breaking commitment
```

Class coupling:

$$
\begin{aligned}
K_{\mathcal{C}}(x,\xi)
&=
\varepsilon_{\mathcal{C}}
+
\left(
1-\varepsilon_{\mathcal{C}}
\right)
\int_{\mathbb{P}(T_x\mathcal{M})}
\exp
\left(
-
\frac
{
d_{\mathbb{P}}(\xi,\eta)^2
}
{
2\sigma_{\mathcal{C}}^2
}
\right)
d\nu_{\mathcal{C}}(\eta)
\end{aligned}
$$

This guarantees:

$$
\begin{aligned}
K_{\mathcal{C}}(x,\xi)
&\geq
\varepsilon_{\mathcal{C}}
\end{aligned}
$$

Agent rule:

Never set ordinary action-fit to zero. A person remains a complete organism. Even off-method experience can train body, nerves, recovery, resources, perception, and identity.

---

## 14. General organism contribution floor

Class XP includes general adaptation plus method-specific interpretation.

$$
\begin{aligned}
\rho_{\mathcal{C}}(L)
&=
\rho_\infty
+
(\rho_0-\rho_\infty)
\exp
\left(
-
\delta
\mathscr{I}_{\mathcal{C}}
\eta_{\mathcal{C}}(L)
\right)
\end{aligned}
$$

Interpretation:

- Early levels allow more general organismic experience to feed the class.
- Higher-rarity classes gradually demand more class-specific embodiment.
- Even late, `rho_infinity` keeps a nonzero general floor.

Agent defaults if numerical scene XP is needed:

| Parameter | Default |
|---|---:|
| `rho_0` | 0.35 |
| `rho_infinity` | 0.12 |
| `delta` | 0.08 |

These are calibration defaults, not metaphysical absolutes. If better resource-system data exists, derive them from the fraction of progression attributed to general body/nervous-system/resource adaptation.

---

## 15. Combat XP adapter


### Combat Adaptation Trace Intake

`combat_defense.md` supplies raw exchange facts through `CombatAdaptationTrace`. This file must combine those facts with owner-resolved resource, injury, perception/neural, novelty, identity, class-method, and recovery states before calculating adaptive evidence.

```text
CombatAdaptationTrace:
  exchangeId
  beatDuration
  actorRoles
  threatExposureFacts
  simultaneousThreatCount
  surpriseOrAttentionSplitFacts
  resolvedContactClass
  resolvedContactQuality
  hpDamageOutputCandidate
  injuryRiskSignal
  forcedExertionFacts
  resourcePressureByActorAndChannel
  interfaceOrAethericInstabilityFacts
  tacticalPatternFacts
  protectionOrIdentityChoiceFacts
  classMethodActionTags
  outcomeContributionTrace
  recoveryDebtCreated
  ownerStatesRequiredBeforeXP:
    resource_system
    embodiment_injury
    perception_information
  routedFrom: combat_defense.md
  routedTo: xp_progression_formulas.md
```

The trace is evidence input, not XP. Do not infer final HP loss, injury severity, crash state, or recovery integration from Combat's candidate values when the owner file has not returned them yet.

Combat XP is not kill XP.

Combat XP is recoverable combat adaptation:

```text
threat exposure
+ physical strain
+ neural pressure
+ resource depletion
+ tactical novelty
+ class-method expression
+ consequence
+ recovery integration
```

For combat scene `e`:

$$
\begin{aligned}
\mathcal{E}_{\mathcal{C}}^{combat}(e,L)
&=
\sum_b
\gamma_0
A_b
H_b
\Theta_e
\Xi_e
\left[
\rho_{\mathcal{C}}(L)
+
(1-\rho_{\mathcal{C}}(L))
K_{\mathcal{C},b}
\right]
+
\sum_{k \in J_e}
\mathcal{J}_{\mathcal{C},k}
\end{aligned}
$$

Where each beat load is:

$$
\begin{aligned}
A_b
&=
\sqrt
{
z_{bio,b}^2
+
z_{neuro,b}^2
+
z_{res,b}^2
+
z_{id,b}^2
+
z_{aether,b}^2
}
\Delta t_b
\end{aligned}
$$

Then:

$$
\begin{aligned}
\Delta XP_{\mathcal{C}}^{combat}(e,L)
&=
XP_{\mathcal{C}}(L)
\left(
1-
\exp
\left[-\mathcal{E}_{\mathcal{C}}^{combat}(e,L)
\right]
\right)
\end{aligned}
$$

Combat beat proxies:

| Channel | Combat proxies |
|---|---|
| Biological | HP lost, injury severity, forced exertion, heavy impacts, bleeding, poison, heat/cold |
| Neural | number of threats, speed of decisions, surprise, pain, fear, attention splitting |
| Resource | Mana spent, Stamina spent, Reserve tapped, failed casts, forced overuse |
| Identity | protecting someone, vow pressure, moral injury, choosing class-defining action under cost |
| Aetheric | domain resistance, interface overuse, spell instability, backlash, hostile metaphysics |

Threat should not be pure level difference. Use effective danger:

$$
\begin{aligned}
\mathcal{T}_e
&=
\frac
{
\text{enemy effective pressure}
}
{
\text{character current tolerance}
}
\end{aligned}
$$

Novelty / repetition modifier:

$$
\begin{aligned}
N_e
&=
\exp
\left(
-
\frac{m_e}{m_0}
\right)
+
N_\infty
\end{aligned}
$$

Consequence can include threat and novelty:

$$
\begin{aligned}
\Xi_e
&=
1
+
\omega_T \ln(1+\mathcal{T}_e)
+
\omega_N N_e
+
\omega_S S_e
\end{aligned}
$$

Where `S_e` is story/identity stakes.


$N_e$ is a novelty weight, not a probability, and may exceed $1$ if $N_\infty>0$. Its scale is absorbed by $\omega_N$.

Agent rule:

If a fight is easy, repeated, safe, and already understood, it gives low XP even if many enemies die. If a fight is dangerous, novel, costly, and forces new adaptation, it gives high XP even if no one dies.

---

## 16. Scene calibration examples

The table below shows how much XP a scene gives if its dimensionless adaptive evidence falls in a given band. These are examples for calibration, not fixed awards.

| Level | Scene calibration band | Adaptive evidence | Threshold fraction | Common XP | Legendary XP |
|---:|---|---:|---:|---:|---:|
| 1 | Low-risk productive practice | 0.040 | 0.0392 | 3.9 | 3.9 |
| 1 | Controlled spar / drill under pressure | 0.100 | 0.0952 | 9.5 | 9.5 |
| 1 | Serious class-relevant challenge | 0.250 | 0.2212 | 22.1 | 22.1 |
| 1 | Dangerous novel combat | 0.450 | 0.3624 | 36.2 | 36.2 |
| 1 | Near-death adaptive breakthrough | 0.750 | 0.5276 | 52.8 | 52.8 |
| 1 | Arc-defining threshold event | 1.250 | 0.7135 | 71.3 | 71.3 |
| 10 | Low-risk productive practice | 0.040 | 0.0392 | 74.1 | 95.3 |
| 10 | Controlled spar / drill under pressure | 0.100 | 0.0952 | 179.8 | 231.3 |
| 10 | Serious class-relevant challenge | 0.250 | 0.2212 | 418.0 | 537.7 |
| 10 | Dangerous novel combat | 0.450 | 0.3624 | 684.8 | 880.8 |
| 10 | Near-death adaptive breakthrough | 0.750 | 0.5276 | 997.2 | 1,282.5 |
| 10 | Arc-defining threshold event | 1.250 | 0.7135 | 1,348.4 | 1,734.3 |
| 20 | Low-risk productive practice | 0.040 | 0.0392 | 204.5 | 282.2 |
| 20 | Controlled spar / drill under pressure | 0.100 | 0.0952 | 496.4 | 685.0 |
| 20 | Serious class-relevant challenge | 0.250 | 0.2212 | 1,153.8 | 1,592.2 |
| 20 | Dangerous novel combat | 0.450 | 0.3624 | 1,890.1 | 2,608.4 |
| 20 | Near-death adaptive breakthrough | 0.750 | 0.5276 | 2,752.1 | 3,797.9 |
| 20 | Arc-defining threshold event | 1.250 | 0.7135 | 3,721.6 | 5,135.8 |

Interpretation:

- At level 20, a serious class-relevant challenge with evidence `0.25` gives a Common class about `1153.8` XP and a Legendary class about `1592.2` XP.
- This is not because Legendary is being rewarded more. It is because the Legendary threshold is larger, and the same threshold fraction corresponds to a larger absolute number.
- If a scene is less aligned with the Legendary method, its adaptive evidence for that class should be lower through `K_C`, even though the threshold table is higher.

---

## 17. Energy cost link

Class rarity also increases energy burden for signature abilities.

Use the same information-rarity architecture:

$$
\begin{aligned}
Cost_{ability}
&=
Cost_0
\cdot
\exp
\left(
\zeta
\mathscr{I}_{\mathcal{C}}
\eta_{\mathcal{C}}(L)
\right)
\cdot
Scale^\gamma
\cdot
Resistance
\cdot
Instability
\end{aligned}
$$

Rules:

- Signature abilities express rarity burden more strongly than ordinary actions.
- A Legendary class can be real before its marquee ability is affordable.
- This preserves the Marcus/Realmwalker problem: he owns the class before his body/resources can fuel world-crossing.

---

## 18. Agent guardrails

Agents must not:

1. Reintroduce recurring bonus attribute points from class rarity.
2. Reintroduce the retired `BaseXP(L) × fixed rarity multiplier` model.
3. Treat rarity as raw combat superiority.
4. Make off-method experience produce zero class progress.
5. Treat XP as kill count.
6. Award full XP for unrecovered trauma or damage without integration.
7. Collapse class, Domain, Interface, Skill Affinity, and Spell Skill Mastery into one variable.
8. Duplicate these formulas into `classes.md`, `mechanics.md`, or `resource_system.md`.
9. Use `Exceptional` as the current class-XP rarity unless explicitly preserving historical text.
10. Calculate XP directly inside `combat_defense.md` from an exchange result.

Agents should:

1. Use this file for XP thresholds, rarity burden, adaptive evidence, scene XP, and combat XP.
2. Use `classes.md` for class taxonomy, activation, and class profiles.
3. Use `resource_system.md` for pools, depletion, Reserve conversion, crash states, and final recovery values.
4. Use `mechanics.md` for independent tier ladders.
5. Use prevalence assumptions to derive rarity information.
6. Use display equations with `$$ ... $$` and align the main relational operator.
7. Round design tables to nearest tenth; let the Interface round visible values to whole numbers.
8. Treat `Epic` as the renamed old `Exceptional` class-XP tier.
9. Treat `Fabled` as the inserted bridge tier before Legendary.
10. Keep cumulative adaptive evidence nonnegative in stochastic implementations.

---

## 19. Reference implementation for threshold table

```python
import math

LEVEL_ONE_XP = 100.0
COMMON_LEVEL_20_XP = 5216.0
LEGENDARY_LEVEL_20_RATIO = 1.38

prevalence = {
    "Common": 1.0,
    "Uncommon": 1 / 10,
    "Rare": 1 / 100,
    "Epic": 1 / 1000,
    "Fabled": 1 / 100000,
    "Legendary": 1 / 1000000,
    "Mythic": 1 / 100000000,
}

information = {
    rarity: math.log(prevalence["Common"] / p)
    for rarity, p in prevalence.items()
}

D0 = 2.5177067041177548
beta = 0.0756920570564890

def V0(level):
    return level ** D0

def eta(level, info):
    if info == 0:
        return 0.0
    accumulated = math.log(V0(level) / V0(1))
    return 1 - math.exp(-accumulated / (1 + info))

def V_class(level, info):
    return V0(level) * math.exp(beta * info * eta(level, info))

def xp_to_next(level, rarity):
    info = information[rarity]
    numerator = V_class(level + 1, info) - V_class(level, info)
    denominator = V_class(2, info) - V_class(1, info)
    return LEVEL_ONE_XP * numerator / denominator
```

---

## 20. Reference implementation for scene XP examples

```python
import math

def scene_xp(threshold_xp, adaptive_evidence):
    return threshold_xp * (1 - math.exp(-adaptive_evidence))

scene_bands = {
    "Low-risk productive practice": 0.04,
    "Controlled spar / drill under pressure": 0.10,
    "Serious class-relevant challenge": 0.25,
    "Dangerous novel combat": 0.45,
    "Near-death adaptive breakthrough": 0.75,
    "Arc-defining threshold event": 1.25,
}
```

---

## 21. Remaining open decisions

These remain author decisions:

1. **Unique denominator numeric value:** one per cosmic cycle is locked, but `N_cycle` still needs a number if Unique appears in tables.
2. **Mythic prevalence:** currently one per 100,000,000 Common-class people. Confirm whether this should be one per 10,000,000 instead.
3. **Scene calibration anchor:** choose one reference scene target to solve `gamma_0`, such as: "a serious class-relevant level-10 fight should provide about 20% of the current threshold."
4. **Rarity-name migration:** completed in the current generated `classes.md`; future agents should preserve `Epic` and `Fabled` unless a later author decision changes the ladder.

---
