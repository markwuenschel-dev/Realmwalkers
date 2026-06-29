---
id: luck_fortune
name: Luck / Fortune
kind: system
status: canon
---

# Luck / Fortune

## Status

Canonical cross-system mechanics file.

This file defines Luck/Fortune as a high-order uncertainty system for Dominion Realm. Luck is not a normal resource, not a standard visible attribute, and not a simple roll modifier. Other system files should reference this file and add only local adapter notes.

---

## Core Definition

Luck/Fortune is a cross-cutting uncertainty system that modifies how probability flows through reachable possibility space.

At the deepest layer, possible futures have coherent possibility structure. At the computable/system layer, those possibilities reduce into probability flows over subsystem-specific state spaces.

Luck does not create impossible outcomes. It biases plausible outcomes whose causal paths remain reachable.

Core statement:

$$
\boxed{\text{Luck is probability-flow bias over reachable possibility space, constrained by entropy cost.}}
$$

More complete statement:

$$
\boxed{\text{Luck/Fortune shapes coherent possibility amplitudes and their reduced stochastic probability flows.}}
$$

Fortune adds favorable drift. Misfortune adds adverse drift. Volatility increases diffusion toward extreme outcomes.

---

## Scope

Luck/Fortune can affect uncertain outcomes in:

* combat
* crafting
* injury and healing
* Reserve backlash
* miscasts and power instability
* loot and material variation
* perception and evidence discovery
* social timing
* logistics and supply chains
* environmental hazards
* faction and strategic uncertainty

Luck/Fortune should not directly replace:

* skill
* attributes
* preparation
* causality
* resource capacity
* intelligent agency
* physical constraints

Luck does not directly increase HP, Mana, Stamina, or Reserve maximums. It may affect uncertain events involving those systems, especially crash outcomes, backlash severity, recovery complications, or failure modes.

---

## Naming

Use **Luck** as common speech.

Use **Fortune** as the cleaner system-facing term when the tone should feel more formal or metaphysical.

Use **Misfortune** for adverse probability bias.

Use **Volatility** for increased uncertainty, variance, and extreme outcomes.

Do not define Luck as a hidden stat with a simple channel formula. It is not like Constitution, Wisdom, Conviction, or Mystery. It is a cross-system probability-flow mechanic.

---

## Main Modes

### Fortune

Fortune biases uncertain outcomes toward favorable reachable states.

Mathematically, Fortune appears as favorable drift through possibility space:

$$
u_{\mathrm{Fortune}}
$$

It moves probability density toward states with higher favorability.

### Misfortune

Misfortune biases uncertain outcomes toward harmful reachable states.

$$
u_{\mathrm{Misfortune}}
\approx
-u_{\mathrm{Fortune}}
$$

Misfortune does not make impossible disasters happen. It makes the worst plausible branch more likely.

### Volatility

Volatility does not simply mean good luck or bad luck. It increases spread, instability, and extreme outcomes.

In the probability-flow layer, Volatility modifies diffusion:

$$
D \rightarrow D+\Sigma_L
$$

High Volatility can produce miraculous saves and absurd catastrophes from the same underlying instability.

---

## Passive vs Active Luck

Passive and active Luck are not separate systems. They are different sources of the same probability-bias terms.

For subsystem $X$:

$$
\begin{aligned}
u_{L,X}
&=
u_{\mathrm{passive},X}
+
u_{\mathrm{active},X}
+
u_{\mathrm{field},X}
+
u_{\mathrm{curse},X}
\end{aligned}
$$

Where:

* $u_{\mathrm{passive},X}$ = innate or ambient Fortune
* $u_{\mathrm{active},X}$ = deliberate Luck/Fate manipulation
* $u_{\mathrm{field},X}$ = local blessed, cursed, or probability-distorted zone effect
* $u_{\mathrm{curse},X}$ = imposed Misfortune or fate-binding effect

Passive Luck is opportunistic and usually low-control.

Active Luck is controlled probability steering and should incur cost, strain, debt, backlash, or instability when it bends outcomes far from baseline.

---

## Subsystem Possibility Space

Every local subsystem $X$ has its own possibility space:

$$
\mathcal{M}_X
$$

A point in that space is:

$$
z\in\mathcal{M}_X
$$

The meaning of $z$ depends on the subsystem.

Combat:

$$
\begin{aligned}
z_{\mathrm{combat}}
&=
(
\mathrm{aimError},
\mathrm{timingError},
\mathrm{guardAngle},
\mathrm{woundPath},
\mathrm{organProximity},
\mathrm{footingStability}
)
\end{aligned}
$$

Crafting:

$$
\begin{aligned}
z_{\mathrm{craft}}
&=
(
\mathrm{purity},
\mathrm{resonance},
\mathrm{defectDensity},
\mathrm{thermalStress},
\mathrm{catalystAlignment},
\mathrm{enchantmentStability}
)
\end{aligned}
$$

Reserve backlash:

$$
\begin{aligned}
z_{\mathrm{reserve}}
&=
(
\mathrm{strainLoad},
\mathrm{organStress},
\mathrm{manaDeficit},
\mathrm{staminaDeficit},
\mathrm{interfaceCoherence},
\mathrm{soulShear},
\mathrm{recoveryMargin}
)
\end{aligned}
$$

Social systems:

$$
\begin{aligned}
z_{\mathrm{social}}
&=
(
\mathrm{trust},
\mathrm{suspicion},
\mathrm{timing},
\mathrm{witnesses},
\mathrm{rumors},
\mathrm{incentives},
\mathrm{perceivedRisk}
)
\end{aligned}
$$

Logistics:

$$
\begin{aligned}
z_{\mathrm{logistics}}
&=
(
\mathrm{stockpiles},
\mathrm{routeRisk},
\mathrm{weatherDelay},
\mathrm{spoilage},
\mathrm{laborAvailability},
\mathrm{sabotageRisk},
\mathrm{patrolOverlap}
)
\end{aligned}
$$

The canonical Luck model stays the same. Only the local state variables change.

---

## Deep Coherent Possibility Layer

Possible futures may be represented as complex possibility amplitudes:

$$
\psi_X(z,t)=A_X(z,t)e^{i\phi_X(z,t)}
$$

Where:

* $A_X(z,t)$ = amplitude strength of a possible future
* $\phi_X(z,t)$ = phase/alignment of that possible future
* $z$ = state in subsystem possibility space
* $t$ = time

Observable probability density arises from:

$$
p_X(z,t)=|\psi_X(z,t)|^2
$$

This means favorable and harmful possibilities can reinforce, cancel, phase-align, or phase-disrupt before reducing into ordinary probabilities.

A deeper mixed-state form may use a density operator:

$$
\rho_X
$$

with evolution:

$$
\begin{aligned}
\frac{\partial \rho_X}{\partial t}
&=
-i[\mathcal{H}_X+\mathcal{V}_{L,X},\rho_X]
+
\mathcal{D}_X[\rho_X]
\end{aligned}
$$

Where:

* $\rho_X$ = possibility-state density operator
* $\mathcal{H}_X$ = baseline evolution of possible futures
* $\mathcal{V}_{L,X}$ = Luck/Fortune perturbation operator
* $\mathcal{D}_X$ = decoherence, noise, chaotic leakage, or environmental uncertainty
* $[\ ,\ ]$ = commutator

The observed probability density is recovered from:

$$
p_X(z,t)=\rho_X(z,z,t)
$$

Use the complex/amplitude layer for high-order metaphysical explanation, Fate/Luck powers, Mystery interactions, probability interference, prophecy distortion, and coherent possibility manipulation.

Do not force every mundane event to explicitly solve the amplitude layer. The amplitude layer is the deeper truth; the practical computation usually uses the reduced stochastic flow.

---

## Probability-Flow Reduction

After decoherence, coarse-graining, unresolved microstates, environmental interaction, or Interface compression, the complex possibility layer reduces into a stochastic probability-flow model.

For subsystem $X$:

$$
\begin{aligned}
\frac{\partial p_X}{\partial t}
&=
-\operatorname{div}*{\mathcal{M}*X}\left((b_X+u*{L,X})p_X\right)
+
\frac{1}{2}\Delta*{\mathcal{M}_X}(D_Xp_X)
+
\mathcal{J}_X[p_X]
\end{aligned}
$$

Where:

* $p_X(z,t)$ = probability density over subsystem possibility space
* $b_X(z,t)$ = baseline causal drift
* $u_{L,X}(z,t)$ = Luck/Fortune/Misfortune drift
* $D_X(z,t)$ = uncertainty, noise, variance, diffusion
* $\Sigma_L$ may be added to $D_X$ for Volatility
* $\mathcal{J}_X[p_X]$ = jumps, thresholds, collapses, discrete transitions, table-like events, or failure-basin capture
* $\operatorname{div}_{\mathcal{M}_X}$ = divergence on the relevant possibility manifold
* $\Delta_{\mathcal{M}_X}$ = diffusion/Laplacian operator on the relevant possibility manifold

Plain meaning:

$$
\boxed{\text{Fortune changes drift. Misfortune changes drift in the harmful direction. Volatility changes diffusion.}}
$$

Local events resolve through jumps, thresholds, and classifiers.

---

## Practical Stochastic Simulation Form

For computation, subsystem $X$ can usually be simulated as a stochastic differential equation:

$$
\begin{aligned}
dz_t
&=
[b_X(z_t,t)+u_{L,X}(z_t,t)]dt
+
\sigma_X(z_t,t)dW_t
\end{aligned}
$$

Where:

* $z_t$ = current state in local possibility space
* $b_X$ = baseline causal drift
* $u_{L,X}$ = Luck/Fortune drift
* $\sigma_XdW_t$ = stochastic noise
* $dW_t$ = random fluctuation term

A simple numerical update:

$$
\begin{aligned}
z_{t+\Delta t}
&=
z_t
+
[b_X(z_t,t)+u_{L,X}(z_t,t)]\Delta t
+
\sigma_X(z_t,t)\epsilon\sqrt{\Delta t}
\end{aligned}
$$

where:

$$
\epsilon\sim \mathcal{N}(0,1)
$$

This allows continuous outcomes instead of simple discrete roll tables.

Final subsystem results are produced by classifiers:

$$
Result_X = Classify_X(z_{\mathrm{final}})
$$

Examples:

* wound depth and organ proximity classify into graze, serious wound, maiming wound, or lethal wound
* crafting purity and defect density classify into flawed, stable, excellent, or rare variant
* Reserve strain trajectory falls into crash basin, blackout basin, interface failure basin, or soul-injury basin
* logistics state classifies into on-time arrival, delay, spoilage, ambush, shortage, or breakdown

Discrete labels can exist, but they should usually be final classifications of continuous simulations, not the whole underlying system.

---

## Luck Drift

A useful local expression:

$$
\begin{aligned}
u_{L,X}(z,t)
&=
\lambda_L
R_X(z,t)
\nabla_{\mathcal{M}_X}U_X(z,t)
\end{aligned}
$$

Where:

* $\lambda_L$ = Luck strength
* $R_X(z,t)$ = reachability/plausibility gate
* $U_X(z,t)$ = favorability function for the relevant character, object, side, or system
* $\nabla U_X$ = direction of more favorable outcomes

Fortune:

$$
\begin{aligned}
u_{\mathrm{Fortune},X}
&=
+\lambda_L
R_X
\nabla U_X
\end{aligned}
$$

Misfortune:

$$
\begin{aligned}
u_{\mathrm{Misfortune},X}
&=
-\lambda_M
R_X
\nabla U_X
\end{aligned}
$$

Volatility:

$$
D_X
\rightarrow
D_X+\Sigma_{L,X}
$$

Plain meaning:

$$
\boxed{\text{Fortune drifts uphill in favorability. Misfortune drifts downhill. Volatility widens the spread.}}
$$

---

## Entropy Cost

Forced Luck manipulation must have a cost.

Baseline probability distribution:

$$
p_0(z,t)
$$

Luck-altered distribution:

$$
p_L(z,t)
$$

Information-distance / entropy distortion:

$$
\begin{aligned}
D_{KL}(p_L|p_0)
&=
\int_{\mathcal{M}_X}
p_L(z,t)
\ln
\left(
\frac{p_L(z,t)}{p_0(z,t)}
\right)
dz
\end{aligned}
$$

Cost:

$$
\begin{aligned}
C_L
&=
\kappa D_{KL}(p_L|p_0)
+
\gamma\int_{t_0}^{t_1}|u_{L,X}(t)|^2dt
\end{aligned}
$$

Meaning:

* small coincidence = low cost
* strong lucky break = higher cost
* forced miracle = severe cost
* impossible outcome = forbidden or catastrophic if another power tries to violate the constraint

Entropy cost may appear as:

* fatigue
* backlash
* Misfortune debt
* probability recoil
* fate instability
* Reserve strain
* soul/interface stress
* local bad-luck compensation
* corruption of the active Luck effect
* loss of control over future variance

Passive Luck may not charge an obvious conscious cost, but extreme passive deviations may still create local instability, especially if amplified by artifacts, curses, or active powers.

---

## Reachability and Causal Topology

Luck cannot create unreachable outcomes.

Let $\operatorname{supp}(p)$ be the support of a probability distribution: the set of outcomes with nonzero probability.

Luck must obey:

$$
\begin{aligned}
\operatorname{supp}(p_L)
&\subseteq
\operatorname{Reach}(\operatorname{supp}(p_0))
\end{aligned}
$$

Plain rule:

$$
\boxed{\text{Luck can move probability through causally reachable branches. It cannot place probability into impossible futures.}}
$$

If:

$$
p_0(z,t)=0
$$

because no causal path exists, then:

$$
p_L(z,t)=0
$$

Examples:

* Luck can make an arrow hit a buckle instead of a throat if both trajectories remain plausible.
* Luck cannot make an already-embedded arrow vanish.
* Luck can make a nearly stable potion settle cleanly.
* Luck cannot make incompatible materials become compatible without another power changing the material conditions.
* Luck can make a patrol look away at the right moment if distraction/timing uncertainty exists.
* Luck cannot control an intelligent agent’s mind unless a separate social, psychic, or power-expression mechanism exists.

---

## Chaos, Bifurcations, and Tipping Points

Luck is strongest near unstable branch points.

Systems with high sensitivity to initial conditions can show large outcomes from small perturbations.

Examples:

* blade angle nearly intersects artery
* potion reaction nearly stabilizes
* guard nearly notices a shadow
* enemy almost chooses the wrong target
* bridge almost fails
* spellform nearly collapses
* Reserve crash approaches multiple failure basins

Luck should matter most when outcomes are marginal, chaotic, unstable, or uncertain.

Luck should matter least when outcomes are deterministic, fully constrained, causally locked, or overwhelmingly decided by skill/power difference.

Core rule:

$$
\boxed{\text{Luck has leverage where uncertainty has structure.}}
$$

---

## Active Luck / Fate Control

Active Luck users do not simply choose outcomes. They steer probability flow.

Control form:

$$
\begin{aligned}
u_{\mathrm{active},X}^*
&=
\arg\min_{u}
\int_{t_0}^{t_1}
\left[
-\mathbb{E}*{p_L}[U_X(z)]
+
\kappa |u(z,t)|^2
+
\eta D*{KL}(p_L|p_0)
\right]dt
\end{aligned}
$$

Meaning:

The user seeks the most favorable reachable probability flow at the lowest distortion cost.

Active Luck can include:

* phase alignment of favorable possibilities
* destructive interference against harmful possibilities
* drift toward favorable basins
* avoidance of catastrophic failure basins
* selection of least-bad reachable outcomes
* temporary suppression of adverse volatility
* redirecting Misfortune into a less damaging branch

Active Luck should not become free outcome selection. It is controlled drift under cost and reachability constraints.

---

## Local Event Shortcut

For small discrete event approximations, the local shortcut may be:

$$
\begin{aligned}
p_i'
&=
\frac{
p_i e^{\beta L U_i}
}{
\sum_k p_k e^{\beta L U_k}
}
\end{aligned}
$$

Where:

* $p_i$ = baseline probability of outcome $i$
* $p_i'$ = Luck-modified probability
* $L$ = Luck/Fortune strength
* $U_i$ = favorability of outcome $i$
* $\beta$ = sensitivity coefficient

This is not the core model. It is a compressed single-event approximation of the deeper probability-flow system.

Use it only when a simple finite outcome resolution is sufficient.

---

## Interface Display

The Interface may hide Luck entirely, partially expose it, or describe it indirectly.

Possible outputs:

```text
Probability deviation detected.
```

```text
Outcome shifted within plausible range.
```

```text
Fortune pressure increased.
```

```text
Misfortune accumulation detected.
```

```text
Local probability field unstable.
```

```text
Volatility spike detected.
```

```text
Fate distortion exceeded safe threshold.
```

```text
Entropy debt acquired.
```

```text
Reachability constraint prevented outcome selection.
```

Avoid making Luck read like a normal stat increase unless the story specifically introduces Fortune as a measurable system category.

Do not present Luck as:

```text
Luck +1 = +2% crit chance
```

unless using an intentionally simplified game-like Interface projection.

---

## Computational Reduction

If a computer or author-side tool is calculating outcomes, prefer continuous stochastic simulation over hand-built roll tables.

Recommended practical structure:

1. Define local state vector $z_X$.
2. Define baseline drift $b_X$.
3. Define noise/diffusion $\sigma_X$ or $D_X$.
4. Define favorability function $U_X(z)$.
5. Define reachability gate $R_X(z)$.
6. Define Luck drift $u_{L,X}$.
7. Simulate trajectories.
8. Classify final state.
9. Apply active cost if probability was deliberately forced.

Minimal numerical update:

$$
\begin{aligned}
z_{t+\Delta t}
&=
z_t
+
[b_X(z_t,t)+u_{L,X}(z_t,t)]\Delta t
+
\sigma_X(z_t,t)\epsilon\sqrt{\Delta t}
\end{aligned}
$$

Then:

$$
Result_X=Classify_X(z_{\mathrm{final}})
$$

Use Monte Carlo trajectories, stochastic differential equation approximations, particle simulation, Markov/state-transition models, or basin/attractor simulations as needed.

Roll tables may remain as final labels or fallback approximations, but they should not be the canonical Luck model.

---

## Subsystem Adapter Pattern

Every subsystem that uses Luck should define only local adapter details.

Required adapter fields:

1. **Canonical reference**

   * State that this subsystem uses `luck_fortune.md`.

2. **Local possibility state**

   * Define $z_X$: the relevant continuous/discrete variables.

3. **Baseline drift**

   * Define what normal causality pushes toward without Luck.

4. **Uncertainty / diffusion**

   * Define what remains uncertain, noisy, chaotic, unstable, or variable.

5. **Favorability function**

   * Define $U_X(z)$: what counts as favorable, and for whom.

6. **Luck interaction**

   * Define how Fortune, Misfortune, and Volatility affect this subsystem.

7. **Reachability constraints**

   * Define what Luck cannot do here.

8. **Result classifier**

   * Define how continuous results become narrative/system outcomes.

9. **Forbidden simplifications**

   * State what Luck must not replace in this subsystem.

10. **Example outcomes**

* Give a few example effects.

Do not duplicate the full canonical equations in every subsystem file. Reference this file and define the local adapter.

---

## Canonical Invariants

These rules should remain consistent across all subsystem files.

1. Luck is not a normal resource.
2. Luck does not directly increase HP, Mana, Stamina, or Reserve maximums.
3. Luck does not create impossible outcomes.
4. Luck acts only where uncertainty remains.
5. Luck is strongest near marginal, chaotic, unstable, or branching outcomes.
6. Fortune biases toward favorable reachable outcomes.
7. Misfortune biases toward harmful reachable outcomes.
8. Volatility increases spread and extreme outcomes.
9. Active Luck incurs entropy/control cost.
10. Complex possibility amplitudes are the deeper coherent layer.
11. Fokker–Planck-style probability flow is the reduced stochastic layer.
12. Continuous simulation is preferred over simple roll tables when computation is available.
13. Subsystem equations may look different locally, but they should reduce from the same architecture.
14. Local adapters define variables and constraints; they do not redefine Luck.
15. The Interface displays approximations, not the full metaphysical truth.
