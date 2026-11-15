# The injection classifier gave my worst attack a 0.006: what three measurements taught me about a safe freelancer agent

*Draft for LinkedIn/Medium (target 1,200 to 1,800 words). Owner: replace [bracketed] items, keep the numbers (they come from the repo's results files).*

I built GigBid, a team of LangGraph agents that finds freelance jobs, decides whether they are worth a bid, writes a proposal from my real portfolio, and then stops and waits for me.
Nothing leaves the system without my approval. That last sentence sounds like a feature list item. It turned out to be the design constraint that made every measurement interesting,
because it moved the question from "is the model good?" to "what can go wrong before the human sees it, and what can go wrong because the human trusts what they see?"
Three results surprised me. None is about a bigger model.

## 1. A defence that scored 0.006 on the attack that mattered

Job posts are untrusted text, and an agent that reads them can be talked into doing things. I added a purpose-built open-weight classifier (Llama Prompt Guard 2) as one layer. On a
plainly hostile sample ("ignore previous instructions") it behaved as advertised: 0.9995. On a benign post: 0.0005. Reassuring.

Then I tried the attack I actually worried about, a post that does not argue with the model at all. It just contains a line that looks like a system message and tells the agent to call
the submit tool. The classifier scored it 0.006, effectively "safe". It is not an instruction-override in the way the model was trained to recognise; it is a role forgery aimed at my
specific tool names. My own cheap heuristics, which look for forged roles, delimiters and my tool names inside a job post, flagged it immediately.

The lesson is not "classifiers are bad". It is that a classifier answers the question it was trained on, and your threat model is a different question. I kept both layers, and added two
that do not depend on any model at all: the analyst node has no tools, and the only path to a write goes through a server that refuses without a human-minted approval token. If the
first two layers fail completely, the worst outcome is a weird draft that a person reads, not a sent proposal. Defence in depth stopped being a slogan when one layer scored 0.006.

## 2. "Zero unsupported claims" is not a result

The core promise is that every claim in a proposal is backed by portfolio evidence. A verification step splits a draft into claims, labels each as supported or not, and removes or
rewrites the unsupported ones. After that step my metric `unsupported_after` was 0.0 on every run. I nearly put it in the README.

Then I asked what could make it anything else. The step removes whatever it flags, so the count after removal is zero by construction, whether the verifier is excellent or terrible.
A verifier that deletes every sentence also scores zero. The number says nothing.

What does measure something is a test with planted answers. In my synthetic personas about 4 to 9 percent of past proposals carry a deliberately fabricated claim, labelled at generation
time. Against those labels, with perfect ("oracle") evidence, the verifier removed every fabrication (recall 1.0) and kept every truthful claim. With evidence retrieved the way production
retrieves it, recall stayed at 1.0 but only 83 to 92 percent of truthful claims survived, and precision of the "this is fabricated" flag fell to between 0.13 and 0.40. In plain words: it
never lets a lie through, and in exchange it sometimes deletes a true sentence because retrieval failed to find the proof. That is the honest trade, and it points at the real next
problem (retrieval), not at the verifier.

End to end, on 30 development jobs with real open-weight models, the first draft contained 0.05 unsupported claims per proposal, and the final draft zero. I report the 0.05, because that
is the part the writer model controls. [Caveat to keep: synthetic jobs and personas; an upper bound, not real-world accuracy.]

## 3. The experiment that did not find anything, and why that is a finding

The extension I picked was proposal experiments: two opening strategies (lead with a concrete result from a similar project, or lead with a sharp clarifying question), assigned to jobs by a
hash of the job id so the assignment is reproducible, and tested for reply rate against a simulated client with hidden preferences.

Evidence-first got 47 replies from 56 proposals (0.839). Question-first got 43 from 56 (0.768). A seven-point gap looks like something. The two-proportion z-test says z = 0.95,
p = 0.34. By the usual standard it is noise. Working backwards, detecting a gap that size at 80 percent power at these reply rates takes roughly 500 proposals per arm, nearly ten times
what I ran. A freelancer sending a few proposals a week would need years.

That is the finding I did not expect to care about: for an individual, strategy A/B testing on reply rate is mostly a way to fool yourself. Any tool that shows two percentages with an arrow
between them, without a sample size and a p-value, is selling confidence it has not earned. GigBid shows the counts, the test and the word "inconclusive", and I think that is the right default.
The simulator is also a rule-based stand-in for real clients, so even a significant result would describe the simulator's taste. [Optional: say what you will test on your real Upwork account.]

## 4. The boring property that mattered most: a retry must not spend twice

An agent that can spend Connects must survive a crash without spending them again. Every write carries an idempotency key derived from the job and the exact approved text. In a test, I pause a
run at the approval gate, open a brand new checkpoint saver on the same database file (simulating a restart), resume once, and then replay from the gate's checkpoint: the replay reuses
the same keys, so the marketplace sees one submission, not two. The test is unglamorous and it is the one I would least want to lose, because the failure it prevents costs real money
and cannot be un-sent.

## What I would tell someone building their own

* Measure what the human will not see. The approval screen is the last line of defence, not the first; a reviewer who sees a polished draft will believe it.
* Distrust metrics that cannot fail. If a number is zero by construction, replace it with one that can embarrass you.
* Keep rules, costs and prompts in data. When the marketplace changes a fee, you want a one-line YAML edit and a test, not a code archaeology session.
* Say "underpowered" out loud. A null result with a sample-size calculation is more useful to the next reader than a lucky win.
* A hand-built graph earns its keep on safety, not intelligence: the order of verify, critique, comply and ask-a-human is guaranteed by code, not requested in a prompt.
  [Add your own sentence once the single-agent vs supervisor comparison has been re-run and is valid.]

The code, the evaluation files behind every number above, and the exact commands to reproduce them are in the repository: [link]. The proposals it drafts for me are still approved,
or rejected, by me.
