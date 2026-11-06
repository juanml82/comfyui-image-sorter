# Comfyui Image Sorter

## About

So you like to use comfyui to create images. And you begin to accumulate thousands upon thousands of images, maybe in your comfyui/output folder, maybe you've moved them somewhere else.
And lots of them are different iterations of the same prompt, improved and played with over newer generations. Image galleries made to extract and tag comfyui's prompts lets you keep them in the same folder and then filter them by tag, organizing them.
But I wanted to sort my own into subfolders. So I've vibecoded this project, which you may find of use if you also prefer to sort them rather than simply tag them.
This project does that: sorts your images into different folders based on their subject, actions and location. 

## Features
- Extracts prompts from ComfyUI workflows (traces back through sampler → encode → concatenation nodes)
- Tags images via LLM (prompt-based *or* vision-based)
- Canonicalizes + merges tag clusters into subject / action / location
- Interactive HTML preview gallery for manual cluster adjustments
- One-click move into subfolders

 Full instructions below

## Installation

### Linux
  
```bash

git clone https://github.com/juanml82/comfyui-image-sorter.git
cd comfyui-image-sorter
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

```

### Mac OS
```bash
git clone https://github.com/juanml82/comfyui-image-sorter.git
cd comfyui-image-sorter
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Windows (command line)

```cmd

git clone https://github.com/juanml82/comfyui-image-sorter.git
cd comfyui-image-sorter
python -m venv venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

## Running

Either use the provided run.sh or run.bat files or activate the venv and then run the gui. You could manually run each script one after the other if you want, too.

### Linux & Mac
```bash
source ./venv/bin/activate
python gui.py
```

### Windows
```cmd
call venv\Scripts\activate.bat
python gui.py
```

## Instructions

So, first caveat: Is it fully automated?
**Absolutely not**
Here's why: Using a gallery to filter and look for images means you don't need to match all tags. But grouping them into folders means all tags do need to match. And getting an LLM to do it without user correction invites the problem of overgrouping images, which is harder to solve than undergrouping images.
So it gets you 90% there. And then you have to finetune manually. Or maybe use some sort of sandboxed agent with vision to finish it for you.
Let's look at this with an example: let's say that you want to create images of Aragorn, in Lord of the Rings, as he passes the Argonath statues. Your first prompt is something look "Aragorn, rowing in the Anduin river, looking up in awe". And maybe the model produces Aragorn as Viggo Mortenssen. And maybe you don't want Viggo Mortenssen but rather a new take on Aragorn's looks, based on the books. So you replace "Aragorn" with "ranger", "fifty years old man", "noble man" or something like that, and follow up with a physical description. Or maybe the model doesn't understand what "Anduin" means. Or it doesn't matter. So some prompts have "Anduin", some have "River". And maybe you then decide you do want the Viggo Mortenssen look, but now you're using a different model which doesn't know Mortenssen well, so you use a lora. And rather than "Aragorn" or "Man", you type the lora's keyword. Sound familiar?
(yes, this is a SFW example. Your own gallery may have NSFW examples as well. Same issue)
Grouping by subject, action and location is a good starting way to filter, but "ranger" and "noble" do remain different subjects. And seeking to overgroup all men - or all women - means lots of different subjects now became grouped, defeating the purpose of this project.
So this is how it works:

### **First step**
The first step is to tag the images. There are two paths to do this.
The first path is to extract the prompts from the workflows embedded in the images, and then use an LLM to convert them into tags. This process doesn't just look at the clip text encode node, but traces back from the sampler nodes' positive prompt input, through the actual encode node and, if there is something behind it, to whatever is behind it, including popular concatenation nodes. So it should find the positive prompt in most cases. But comfyui workflows can be complex and varied, so there is no guarantee it's going to find it in all cases. And even if it finds it, if the image was made via image to image or inpainting, the prompt may not have relevant information: for instance, if you have a simple facedetailer workflow where you input the original image from a load image node, then the prompt may simply be "blue eyes". And if you're using lots of danbooru tags, the next steps may not yield good results, as they are really tailored for natural language, even if short.
So, this can't be 100% reliable, but it's the fastest method. Prompt gets extracted, mostly. That's saved into a "extract_results.jsonl" file
The second path is slower, but it doesn't depend on finding a useful positive prompt. It's a straight up LLM vision pass. It's slower - and much slower than a older vit clip pass, but it doesn't depend on a fixed dictionary and actually 'reads' the image. And it returns a "vision_tags.jsonl" file.

### **Second step**
Then a second LLM pass is needed to homogenize - "canonicalize" - the varied tags into a more homogeneous looking group of words.
*In my experience, it's best to run a first pass based on prompts, and then if necessary, apply a vision pass to images that couldn't be sorted, but your mileage may vary.*
As a result, your images have been tagged. But then we end up in the problem I've described above: iterations of the same general idea will be tagged in slightly different ways, because one group of images, for instance, say "Aragorn", another say "Ranger", others say "Noble man" and maybe a few others are a lora activation keyword. 

### **Third step**
So the third pass is a merge pass. First, a Jaccard pass helps join tags clusters. Then an LLM performs going through the resulting group of tags three times, each time figuring out which ones actually refer to the same subjects, actions and settings and unifies those tags. This is what gets you 90% there and it's by design.
First, by grouping them in "who", "what" and "where", it simply doesn't work for abstract images. The system prompts are editable, if this is your case, then open the config.py and change the merge system prompt (and possibly the canonicalize system prompt as well) into something suitable for abstract images.
Second, the LLM may interpret "Aragorn" and "Ranger" as the same person. But it should identify "Ranger" and "Noble man" as *different* people. So images carrying those tags are not going to be merged. And this needs to be by design, because it's easier to group together clusters that ought to be grouped together but aren't, than to ungroup clusters that shouldn't be grouped together.
Third, there may be cases in which clusters shouldn't be grouped, but you want to group them together anyway. You may, for instance, have five groups of images of Tifa Lockhart in different settings and actions. But maybe for this particular character you want all of them in the same folder.

### **Fourth step**
This is the manual pass. It generates a preview.html file based on the "merged.jsonl" file produced in the above step and runs it in a browser in a local server (as there may be thousands of images, this way is more reliable). The preview file is persistent, the server isn't, so if you want to go back to this step after closing the gui, you need to either reopen the gui or run the 4_preview.py script with the venv already activated.
Basically, what you have here is a basic looking image gallery. Image thumbnails are shown, grouped in their respective, alphabetically sorted clusters. A filter at the top lets you filter images by common tags. The idea is that, when you need to further group clusters together, you do it by clicking the check-boxes of the clusters to join and then click the merge button above. Maybe a sandboxed ai agent can do it for you, but you may want to decide on the go how to group them.
Once you're done - and depending on your image collection, this could take a while - you save *overwriting* the original merged.jsonl file. This is critical: **there is no autosave.** The file is located in the same directory as the images you're sorting.

### **Fifth step**
Hit apply and it will create subfolders named by the tags and move the images to their respective subfolder.

### **About GUI and LLM usage**
The GUI should be straightforward. The image folder is where the images you want to sort are located, the output folder is that, the output folder. Then you have the openai compatible endpoint for the LLM of your choice, model and api key if required.
*Save*. There is no autosave. This writes to the config.py file which can - alternatively - be manually edited instead.

Now, about the LLM.
First, you want them as deterministic as possible, so use a low temperature, even straight zero.
For tagging, including vision tagging, the reasoning process adds a huge amount of time while providing negligible improvement - if the LLM even works. I had Gemma 4 12B thinking for over 2500 tokens to tag a single image. So, by design, it's not meant to be used. The canonicalize scripts deliberately cap the max tokens at 40, which results in the LLM returning "Untagged" if you use an LLM with thinking on. You can manually edit it to a far larger value if you really want to use thinking mode. But I don't recommend it.
So use either an instruct model or an LLM with thinking disabled (-rea off in llama.cpp server, other LLM servers may vary), and ideally something small and fast - Abliterated/Heretic if your image gallery requires it. At the moment of this writing, there is a recent finetune of Qwen 3 VL called Qwen3-VL-8B-Abliterated-Caption-it specifically meant for captioning, and even 4B versions of Qwen can work. I had mixed results with Gemma 4, but that's also an option.
If you're doing vision caption, also make sure to increase the ubatch size (-ub 2048 in llama.cpp server) because you may get OOM if the images are too big to fit the default ubatch size.

The merge pass may benefit from using a reasoning process but, once again, it may take too long. By default, the Max tokens are capped at 42000. If you want to edit it, it's in line 161 of the merge.py script. But a model that can work well without reasoning is, I think, a better alternative - I was able to process over 6000 images with these settings. As for models, I had mixed results with Gemma 4 26B A4B, so I think a dense model in the 12B range works better - 4B active parameters may be too little.
### TL,DR

Get an LLM endpoint from the server of your choice. 
Pick a small, fast, dense LLM. Possibly abliterated, prefer instruct models.
Disable thinking, zero temperature, increase ubatch size for vision passes.
Input your image folders and intended destination folders.
Input the LLM endpoint url (remember to add /v1 at the end), model and key if needed.
Choose either go through prompts or a though a vision pass.
Run steps 1-4.
The preview.html opens automatically.
Make manual adjustments as needed.
Hit apply.
## Contributing

Feel free to fork or add PRs if you like.

## License

This project is licensed under [MIT](LICENSE).

## Support

If this saved you time, consider buying me a coffee ☕

<a href="https://ko-fi.com/juanml82"> <img src="https://ko-fi.com/img/githubbutton_sm.svg" alt="Buy me a coffee on Ko-fi" /> </a>
