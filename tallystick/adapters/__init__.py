"""Adapters: turn a framework's run into a raw tallystick trace.

An adapter is a recorder. It watches an agent framework do its work and writes
down what an audit needs: which texts entered the run from outside (documents,
tool results), which texts a model wrote, and - the part no framework logs on its
own - what each model call could actually see when it wrote.

Adapters produce raw traces (artifacts + steps, no claims, no entries). They never
take part in a verdict, so unlike the verdict path they may import framework code.
They must not import `tallystick.propose`; recording and proposing are separate
jobs, and a test keeps them that way.

    from tallystick.adapters.langchain import TraceRecorder
    rec = TraceRecorder()
    chain.invoke(question, config={"callbacks": [rec]})
    rec.save("raw.json")            # then: tallystick propose raw.json -o posted.json
"""
