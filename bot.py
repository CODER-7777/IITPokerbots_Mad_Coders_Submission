"""
Mad Coders — Sneak Peek Hold'em Poker Bot
IIT Pokerbots 2026 Competition

A competitive poker bot implementing Monte Carlo simulation with eval7,
modified Chen formula for pre-flop evaluation, and adaptive strategy.

Competition Result: Rank #127 / 467 matches / 315 wins / 152 losses (67.5% WR)
"""

from pkbot.actions import ActionFold, ActionCall, ActionCheck, ActionRaise, ActionBid
from pkbot.states import GameInfo, PokerState
from pkbot.states import NUM_ROUNDS, STARTING_STACK, BIG_BLIND, SMALL_BLIND
from pkbot.base import BaseBot
from pkbot.runner import parse_args, run_bot

import eval7
import random


# ─── Constants ────────────────────────────────────────────────────────────────

ALL_CARDS = [r + s for r in '23456789TJQKA' for s in 'cdhs']

RANK_VAL = {
    '2': 0, '3': 1, '4': 2, '5': 3, '6': 4, '7': 5,
    '8': 6, '9': 7, 'T': 8, 'J': 9, 'Q': 10, 'K': 11, 'A': 12,
}


# ─── Hand Evaluation ─────────────────────────────────────────────────────────

def monte_carlo_equity(my_hand, board, opp_known=None, num_sims=100):
    """
    Estimate win probability via Monte Carlo simulation using eval7.

    Runs `num_sims` random rollouts, dealing unknown opponent hole cards and
    remaining board cards, then compares hand rankings.

    Args:
        my_hand:   List of hole card strings, e.g. ['Ah', 'Kd']
        board:     List of community card strings
        opp_known: List of known opponent card strings (from auction peek)
        num_sims:  Number of simulations to run

    Returns:
        Win probability as float in [0.0, 1.0]
    """
    if opp_known is None:
        opp_known = []

    used = set(my_hand) | set(board) | set(opp_known)
    deck = [eval7.Card(s) for s in ALL_CARDS if s not in used]

    my_cards  = [eval7.Card(s) for s in my_hand]
    board_e7  = [eval7.Card(s) for s in board]
    opp_e7    = [eval7.Card(s) for s in opp_known]

    board_need = 5 - len(board_e7)
    opp_need   = 2 - len(opp_e7)
    total_need = board_need + opp_need

    if total_need > len(deck):
        return 0.5

    wins = ties = 0
    for _ in range(num_sims):
        sample    = random.sample(deck, total_need)
        opp_cards = opp_e7 + sample[:opp_need]
        full_board = board_e7 + sample[opp_need:]

        my_score  = eval7.evaluate(my_cards + full_board)
        opp_score = eval7.evaluate(opp_cards + full_board)

        if my_score > opp_score:
            wins += 1
        elif my_score == opp_score:
            ties += 1

    return (wins + 0.5 * ties) / num_sims


def preflop_strength(hand):
    """
    Score a 2-card hand using a modified Chen formula.

    Maps hole cards to a strength float in [0.20, 0.90], providing better
    separation than raw Monte Carlo (which clusters around 0.50 pre-flop).

    Args:
        hand: List of 2 card strings, e.g. ['Ah', 'Kd']

    Returns:
        Pre-flop hand strength as float in [0.20, 0.90]
    """
    r1, s1 = RANK_VAL[hand[0][0]], hand[0][1]
    r2, s2 = RANK_VAL[hand[1][0]], hand[1][1]

    if r1 < r2:
        r1, r2 = r2, r1

    high = {12: 10.0, 11: 8.0, 10: 7.0, 9: 6.0}.get(r1, (r1 + 2) / 2.0)
    score = high

    if r1 == r2:
        score = max(score * 2, 5.0)
    else:
        if s1 == s2:
            score += 2.0
        gap = r1 - r2 - 1
        score -= [0, 1, 2, 4, 5][min(gap, 4)] if gap > 0 else 0
        if gap <= 1 and r1 != 12:
            score += 1.0

    return max(0.20, min(0.90, (score + 2.0) / 22.0))


# ─── Bet Sizing ───────────────────────────────────────────────────────────────

def compute_bet_size(strength, pot, my_chips, opp_chips, opp_fold_rate=0.30):
    """
    Calculate bet size as a fraction of pot based on hand strength.

    Uses tiered sizing: small bets with marginal hands, pot-sized bets with
    strong hands, and exploitative over-bets against calling stations.

    Returns:
        Integer bet amount, clamped to [BIG_BLIND, effective_stack]
    """
    max_bet = min(my_chips, opp_chips)

    if   strength > 0.90 and opp_fold_rate < 0.20: frac = 1.50  # exploit stations
    elif strength > 0.88: frac = 1.00
    elif strength > 0.78: frac = 0.60
    elif strength > 0.65: frac = 0.40
    else:                 frac = 0.25

    return max(BIG_BLIND, min(int(frac * pot), max_bet))


# ─── Bot Implementation ──────────────────────────────────────────────────────

class Player(BaseBot):
    """
    Sneak Peek Hold'em poker bot.

    Strategy components:
      • eval7-powered Monte Carlo equity estimation (~100x faster than pure Python)
      • Modified Chen formula for pre-flop hand ranking
      • Vickrey-optimal auction bidding with information-theoretic valuation
      • Adaptive opponent modeling (fold frequency tracking)
      • Position-aware play with IP/OOP adjustments
      • Selective bluffing calibrated to opponent fold rate
      • Board texture awareness (paired board trap avoidance)
      • Dynamic time management (sim count scales with remaining time bank)
    """

    def __init__(self):
        self.opp_peeked = []       # revealed opponent card(s) from auction
        self.opp_folds = 0         # count of observed opponent folds
        self.hands_played = 0      # total hands completed

    # ── Lifecycle Hooks ───────────────────────────────────────────────────

    def on_hand_start(self, game_info: GameInfo, state: PokerState) -> None:
        self.opp_peeked = []
        self._capture_peek(state)

    def on_hand_end(self, game_info: GameInfo, state: PokerState) -> None:
        self.hands_played += 1
        if state.is_terminal and state.payoff > 0:
            if state.my_wager > state.opp_wager:
                self.opp_folds += 1

    # ── Main Decision Function ────────────────────────────────────────────

    def get_move(self, game_info: GameInfo, state: PokerState):
        """Return an Action for the current game state."""

        # Adaptive simulation count based on remaining time bank
        remaining = max(NUM_ROUNDS - game_info.round_num + 1, 1)
        time_per_round = game_info.time_bank / remaining

        if   time_per_round < 0.008: num_sims = 200
        elif time_per_round < 0.015: num_sims = 500
        elif time_per_round < 0.030: num_sims = 1000
        else:                        num_sims = 2500

        # Validate hole cards
        my_hand = state.my_hand
        if not my_hand or len(my_hand) < 2:
            return ActionCheck() if state.can_act(ActionCheck) else ActionFold()

        board = state.board or []
        self._capture_peek(state)

        # ── AUCTION PHASE ─────────────────────────────────────────────────
        # Vickrey auction: winner pays loser's bid to peek one opponent card.
        # Bid based on information value (uncertainty × pot leverage), capped
        # at 10% of stack / 500 chips to prevent auction traps.

        if state.street == 'auction':
            return self._auction_decision(state, my_hand, board, num_sims)

        # ── BETTING PHASE ─────────────────────────────────────────────────
        return self._betting_decision(game_info, state, my_hand, board, num_sims)

    # ── Auction Strategy ──────────────────────────────────────────────────

    def _auction_decision(self, state, my_hand, board, num_sims):
        """Determine auction bid amount."""
        pot       = state.pot
        my_chips  = state.my_chips
        eff_stack = min(my_chips, state.opp_chips)

        wp = monte_carlo_equity(my_hand, board, self.opp_peeked,
                                num_sims=min(num_sims, 500))
        uncertainty = 4.0 * wp * (1.0 - wp)

        if wp < 0.25:
            bid = min(15, my_chips)
        else:
            info_value = uncertainty * (pot + eff_stack * 0.15)

            # Tiered cap: conservative on small pots, moderate on large pots
            if pot <= 120:
                cap_pct = 0.08 if wp > 0.75 else 0.04
            else:
                cap_pct = 0.10 if wp > 0.65 else 0.06
            cap = min(int(my_chips * cap_pct), 500)

            floor = 0 if (pot <= 120 and wp < 0.85) \
                    else min(int(eff_stack * 0.03), my_chips)

            bid = max(floor, int(min(info_value, cap)), 0)

        return ActionBid(min(max(0, int(bid)), my_chips))

    # ── Betting Strategy ──────────────────────────────────────────────────

    def _betting_decision(self, game_info, state, my_hand, board, num_sims):
        """Core betting logic for pre-flop through river."""
        street    = state.street
        my_chips  = state.my_chips
        opp_chips = state.opp_chips
        pot       = state.pot
        cost      = state.cost_to_call
        is_ip     = state.is_bb  # BB acts last post-flop = in position

        # Step 1: Estimate hand strength
        if street == 'pre-flop':
            strength = preflop_strength(my_hand)
        else:
            strength = monte_carlo_equity(my_hand, board, self.opp_peeked,
                                          num_sims=num_sims)

        # Step 2: Position adjustment
        if street != 'pre-flop':
            strength += 0.03 if is_ip else -0.02
            if self.opp_peeked:
                strength += 0.04  # more reliable estimate when we peeked

        # Step 3: Opponent profile
        opp_fold_rate = 0.30
        bluff_chance  = 0.0
        is_maniac     = False

        if self.hands_played > 30:
            opp_fold_rate = self.opp_folds / max(self.hands_played, 1)
            if opp_fold_rate > 0.40:
                bluff_chance = min(0.20, (opp_fold_rate - 0.30) * 0.5)
            elif opp_fold_rate < 0.18:
                is_maniac = True

        # Step 4: Pot odds
        pot_odds = (cost / (pot + cost)) if cost > 0 else 0.0

        # ── Pre-Flop Decisions ────────────────────────────────────────────
        if street == 'pre-flop':
            return self._preflop_action(
                state, strength, cost, pot, my_chips, opp_chips,
                opp_fold_rate, is_maniac,
            )

        # ── Post-Flop: No Bet to Face ────────────────────────────────────
        if state.can_act(ActionCheck):
            return self._check_or_bet(
                state, strength, pot, my_chips, opp_chips,
                opp_fold_rate, bluff_chance, street,
            )

        # ── Post-Flop: Facing a Bet ──────────────────────────────────────
        if cost > 0:
            return self._facing_bet(
                state, strength, cost, pot, pot_odds, my_chips, opp_chips,
                opp_fold_rate, bluff_chance, is_maniac, street, board, my_hand,
            )

        # Fallback
        if state.can_act(ActionCheck): return ActionCheck()
        if state.can_act(ActionCall):  return ActionCall()
        return ActionFold()

    # ── Pre-Flop ──────────────────────────────────────────────────────────

    def _preflop_action(self, state, strength, cost, pot, my_chips, opp_chips,
                        opp_fold_rate, is_maniac):
        """Pre-flop decision logic with re-raise cap."""
        fold_thresh = 0.42 if is_maniac else 0.35

        if strength < fold_thresh:
            if cost == 0 and state.can_act(ActionCheck):
                return ActionCheck()
            if state.can_act(ActionFold):
                return ActionFold()

        elif strength >= 0.55 and state.can_act(ActionRaise):
            # Cap re-raises: don't 5-bet without QQ+/AKs (strength >= 0.80)
            total_pot_bb = (pot + cost) / max(1, BIG_BLIND)
            if total_pot_bb > 30 and strength < 0.80:
                if state.can_act(ActionCall):  return ActionCall()
                if state.can_act(ActionCheck): return ActionCheck()

            ref_pot = max(pot, 2 * BIG_BLIND)
            size = compute_bet_size(strength, ref_pot, my_chips, opp_chips,
                                    opp_fold_rate)
            lo, hi = state.raise_bounds
            return ActionRaise(max(lo, min(size, hi)))

        # Default: call or check
        if state.can_act(ActionCall):  return ActionCall()
        if state.can_act(ActionCheck): return ActionCheck()
        return ActionFold()

    # ── Check or Bet (no bet to face) ─────────────────────────────────────

    def _check_or_bet(self, state, strength, pot, my_chips, opp_chips,
                      opp_fold_rate, bluff_chance, street):
        """Decide whether to check or lead out with a bet."""
        if strength < 0.35:
            # Occasional check-raise bluff
            if (bluff_chance > 0 and strength > 0.20
                    and random.random() < bluff_chance * 0.5
                    and state.can_act(ActionRaise)):
                lo, hi = state.raise_bounds
                return ActionRaise(max(lo, min(int(pot * 0.6), hi)))
            return ActionCheck()

        if strength >= 0.55 and state.can_act(ActionRaise):
            ref_pot = max(pot, 2 * BIG_BLIND)
            size = compute_bet_size(strength, ref_pot, my_chips, opp_chips,
                                    opp_fold_rate)
            # Extract more value when we have information advantage
            if self.opp_peeked and strength > 0.70:
                size = int(size * 1.3)
            lo, hi = state.raise_bounds
            return ActionRaise(max(lo, min(size, hi)))

        return ActionCheck()

    # ── Facing a Bet ──────────────────────────────────────────────────────

    def _facing_bet(self, state, strength, cost, pot, pot_odds, my_chips,
                    opp_chips, opp_fold_rate, bluff_chance, is_maniac, street,
                    board, my_hand):
        """Decide how to respond when facing a bet."""
        is_overbet = cost > max(pot, 40) * 3
        bb_cost = cost / max(1, BIG_BLIND)

        # Implied discount: heavy bets signal polarized ranges
        if not is_maniac:
            if cost > pot:     strength -= 0.10
            if is_overbet:     strength -= 0.15
            if bb_cost > 30:   strength -= 0.10
            if bb_cost > 60:   strength -= 0.10

        # Handle overbets / massive bets
        if is_overbet or bb_cost > 50:
            thresh = 0.50 if is_maniac else 0.70
            if strength > thresh and cost <= my_chips:
                return ActionCall()
            return ActionFold() if state.can_act(ActionFold) else ActionCall()

        # River raise deference: opponents rarely bluff river raises
        if street == 'river' and cost > 0 and strength < 0.82:
            if cost >= pot * 0.40:
                if strength > pot_odds + 0.05:
                    return ActionCall()
                return ActionFold() if state.can_act(ActionFold) else ActionCall()

        # Bloated pot control: don't escalate without a monster
        total_pot_bb = (pot + cost) / max(1, BIG_BLIND)
        if ((total_pot_bb > 80 and strength < 0.75)
                or (street == 'river' and cost > pot * 0.5 and strength < 0.85)):
            if strength > pot_odds + 0.10:
                return ActionCall()
            return ActionFold() if state.can_act(ActionFold) else ActionCall()

        # Board texture: don't re-raise on paired boards without trips
        can_raise = True
        board_ranks = [c[0] for c in board]
        is_paired = any(board_ranks.count(r) >= 2 for r in set(board_ranks))

        if is_paired and cost > 0:
            my_ranks = [c[0] for c in my_hand]
            paired_ranks = [r for r in set(board_ranks) if board_ranks.count(r) >= 2]
            has_trips = (any(r in my_ranks for r in paired_ranks)
                         or (my_ranks[0] == my_ranks[1] and my_ranks[0] in board_ranks))
            if not has_trips:
                can_raise = False

        # Raise for value
        if (strength > max(0.65, pot_odds + 0.15)
                and state.can_act(ActionRaise) and bb_cost < 40 and can_raise):
            call_pot = pot + cost
            size = compute_bet_size(strength, call_pot, my_chips, opp_chips,
                                    opp_fold_rate)
            if self.opp_peeked and strength > 0.70:
                size = int(size * 1.3)
            lo, hi = state.raise_bounds
            return ActionRaise(max(lo, min(size, hi)))

        # Call with adequate equity
        if strength >= pot_odds + 0.03 or (is_overbet and strength > 0.46):
            return ActionCall()

        # Bluff-raise into small bets
        if (bluff_chance > 0 and strength > 0.15
                and cost < pot * 0.3
                and random.random() < bluff_chance * 0.3
                and state.can_act(ActionRaise)):
            lo, hi = state.raise_bounds
            return ActionRaise(max(lo, min(int(pot * 0.75), hi)))

        return ActionFold() if state.can_act(ActionFold) else ActionCall()

    # ── Utility ───────────────────────────────────────────────────────────

    def _capture_peek(self, state: PokerState):
        """Store any revealed opponent cards for Monte Carlo accuracy."""
        if self.opp_peeked:
            return
        try:
            revealed = state.opp_revealed_cards
            if revealed and len(revealed) > 0:
                self.opp_peeked = [c for c in revealed if c and c.strip()]
        except Exception:
            pass


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    run_bot(Player(), parse_args())
